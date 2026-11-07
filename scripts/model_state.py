"""Local, credential-scoped evidence for real Images API calls (no test requests)."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import warnings


class ConfigError(ValueError):
    pass


class RequestFailure(RuntimeError):
    def __init__(self, category, status=None):
        self.category, self.status = category, status
        super().__init__(category + (f" (HTTP {status})" if status is not None else ""))


class TemporaryRequestFailure(RequestFailure):
    """Name also preserves the original CLI's transient batch retry policy."""


def request_failure(exc):
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) else None
    name = type(exc).__name__.lower()
    if status == 429:
        category = "rate_limited"
    elif status in (408, 409) or (status is not None and status >= 500):
        category = "service_error"
    elif "timeout" in name:
        category = "timeout"
    elif "connection" in name:
        category = "connection_error"
    elif status in (401, 403):
        category = "authentication_or_permission"
    elif status in (400, 404, 405, 422):
        category = "request_rejected"  # May be parameters, endpoint, access, or model.
    else:
        category = "request_failed"
    cls = TemporaryRequestFailure if category in {
        "rate_limited", "service_error", "timeout", "connection_error"
    } else RequestFailure
    return cls(category, status)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def context_id(config, model):
    # Never persist the key or an isolated key hash. A changed URL/key/model
    # produces a different scope; old evidence remains historical only.
    value = [config["base_url"].rstrip("/"), config["api_key"], model]
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def state_lock(path):
    lock = path.with_suffix(".lock")
    deadline = time.monotonic() + 5
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ConfigError("Validation state is busy; retry later or inspect its local lock file.") from None
            time.sleep(0.05)
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def load_state(path):
    if not path.exists():
        return {"version": 1, "contexts": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        valid = (isinstance(state, dict) and state.get("version") == 1
                 and isinstance(state.get("contexts"), dict))
        if not valid:
            raise ValueError()
        for events in state["contexts"].values():
            if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
                raise ValueError()
        return state
    except (ValueError, OSError):
        raise ConfigError("Cannot read validation-state.json; inspect it locally (it was not overwritten).") from None


def validate_images(response, expected):
    """HTTP 200/base64 alone do not establish that usable image data was returned."""
    try:
        from PIL import Image
        data = response.data
        if not isinstance(data, list) or len(data) != expected or not data:
            raise ValueError()
        for item in data:
            encoded = item.b64_json
            if not isinstance(encoded, str) or not encoded:
                raise ValueError()
            raw = base64.b64decode(encoded, validate=True)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as picture:
                    if picture.format not in {"PNG", "JPEG", "WEBP"}:
                        raise ValueError()
                    picture.verify()
                with Image.open(io.BytesIO(raw)) as picture:
                    picture.load()
    except ImportError:
        raise ConfigError("Install Pillow to verify image responses before recording success.") from None
    except Exception:
        raise RequestFailure("invalid_image_response") from None


class Recorder:
    def __init__(self, config, path):
        self.config, self.path = config, Path(path)

    def record(self, operation, payload, error=None):
        parameters = {k: payload[k] for k in (
            "n", "size", "quality", "background", "output_format",
            "output_compression", "input_fidelity", "moderation"
        ) if k in payload}
        if operation == "edit":
            image = payload.get("image")
            parameters["input_image_count"] = len(image) if isinstance(image, list) else 1
            parameters["has_mask"] = payload.get("mask") is not None
        event = {"at": now(), "operation": operation, "parameters": parameters,
                 "result": "verified" if error is None else error.category}
        if error is not None and error.status is not None:
            event["http_status"] = error.status
        try:
            # Redact even an unexpected parameter containing the credential.
            if self.config["api_key"]:
                encoded = json.dumps(event).replace(json.dumps(self.config["api_key"])[1:-1], "[REDACTED]")
                event = json.loads(encoded)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with state_lock(self.path):
                state = load_state(self.path)
                key = context_id(self.config, payload["model"])
                events = state["contexts"].setdefault(key, [])
                events.append(event)
                # Keep bounded recent history plus the latest successful evidence
                # for each operation, even after many temporary failures.
                keep = set(range(max(0, len(events) - 100), len(events)))
                for op in ("generate", "edit"):
                    successes = [i for i, e in enumerate(events)
                                 if e.get("operation") == op and e.get("result") == "verified"]
                    if successes:
                        keep.add(successes[-1])
                state["contexts"][key] = [events[i] for i in sorted(keep)]
                atomic_json(self.path, state)
        except (OSError, ValueError):
            # A bookkeeping problem must never cause an image request to repeat.
            import sys
            print("Warning: request completed, but validation evidence could not be saved; inspect local state/lock.", file=sys.stderr)

    def status(self, model):
        state = load_state(self.path)
        events = state["contexts"].get(context_id(self.config, model), [])
        result = {"model": model, "scope": "current URL, key, and model only"}
        for operation in ("generate", "edit"):
            attempts = [e for e in events if e.get("operation") == operation]
            successes = [e for e in attempts if e.get("result") == "verified"]
            result[operation] = {
                "status": "verified" if successes else "unverified",
                "last_success": successes[-1] if successes else None,
                "last_attempt": attempts[-1] if attempts else None,
            }
        return result
