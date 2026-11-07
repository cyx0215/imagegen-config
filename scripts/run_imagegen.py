#!/usr/bin/env python3
"""Run the installed original imagegen CLI with file-based client configuration."""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType
from types import SimpleNamespace
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_state import (ConfigError, Recorder, RequestFailure, atomic_json,
                         request_failure, validate_images)


SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = ""  # Selected from this provider's /models; never guess one.


def read_config(path: Path, *, require_key: bool, require_model: bool = True) -> dict[str, str]:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        # JSON errors can contain excerpts of a secret-bearing file.
        raise ConfigError("Cannot read config as UTF-8 JSON; check the file locally.") from None
    return validate_config(config, require_key=require_key, require_model=require_model)


def validate_config(config, *, require_key, require_model):
    if not isinstance(config, dict) or "api_key" not in config or set(config) - {"base_url", "api_key", "model"}:
        raise ConfigError("Config must contain api_key; base_url and model are optional.")
    config = {"base_url": "", "model": DEFAULT_MODEL, **config}
    for field, value in config.items():
        if not isinstance(value, str) or value != value.strip():
            raise ConfigError(f"Config field {field} must be a string without surrounding whitespace.")
    # Pass an explicit default so OPENAI_BASE_URL can never override a blank URL.
    config["base_url"] = config["base_url"] or DEFAULT_BASE_URL
    config["model"] = config["model"] or DEFAULT_MODEL
    try:
        url = urlsplit(config["base_url"])
        valid_url = (
            url.scheme in {"http", "https"} and bool(url.hostname)
            and url.username is None and url.password is None
            and not url.query and not url.fragment
        )
        url.port  # Validate a supplied port without printing the URL.
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ConfigError("base_url must be an HTTP(S) API base URL without credentials, query, or fragment.")
    if require_key and not config["api_key"]:
        raise ConfigError("api_key is empty. Fill it in the local config file; do not paste it into chat.")
    if require_model and not config["model"]:
        raise ConfigError("No model selected. Run models list, then models select MODEL_ID; no default model is assumed.")
    return config


def configure_interactively(path):
    import getpass
    config = read_config(path, require_key=False, require_model=False)
    print("Enter to keep an existing value. The API key entry is hidden.")
    url = input(f"API base URL [{config['base_url']}] (- resets to the official default): ").strip()
    key = getpass.getpass("API key [keep current if blank]: ").strip()
    values = {**config}
    if url:
        values["base_url"] = "" if url == "-" else url
    if key:
        values["api_key"] = key
    updated = validate_config(values, require_key=True, require_model=False)
    atomic_json(path, values)
    print("URL/key saved locally. Querying /models only; no image test is performed.")
    module = load_original(default_imagegen_dir())
    try:
        listed = fetch_models(updated)
    except RequestFailure as exc:
        print(f"Model discovery failed: {exc}. Credentials saved; run models list/select later.")
        return
    report = describe_models(updated, listed, module, Recorder(updated, path.with_name("validation-state.json")))
    print_safe(report, updated)
    if not any(model["cli_accepts_name"] for model in report["models"]):
        print("No listed model is accepted by the current CLI. Ask Codex to check API compatibility; no model was changed.")
    elif report["selection_required"]:
        print("URL/key configuration is complete. Codex chooses and saves a model during setup or first use; no model entry is required from you.")
    else:
        print("Current model retained. Codex can select another model whenever you ask.")


def create_api_client(config, *, asynchronous=False, timeout=180):
    try:
        from openai import OpenAI, AsyncOpenAI, DefaultHttpxClient, DefaultAsyncHttpxClient
        import httpx
    except ImportError:
        raise ConfigError("Install this skill's requirements in the active Python environment.") from None
    kwargs = dict(api_key=config["api_key"], base_url=config["base_url"],
                  organization="", project="", webhook_secret="", max_retries=0,
                  timeout=httpx.Timeout(timeout, connect=15))
    if asynchronous:
        return AsyncOpenAI(**kwargs, http_client=DefaultAsyncHttpxClient(trust_env=False))
    return OpenAI(**kwargs, http_client=DefaultHttpxClient(trust_env=False))


def accepts_model(module, model):
    # Use the original validator, not a second, potentially drifting allowlist.
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            module._validate_model(model)
        return True
    except SystemExit:
        return False


def fetch_models(config):
    client = create_api_client(config, timeout=30)
    try:
        page = client.models.list()
        if not isinstance(page.data, list):
            raise RequestFailure("invalid_model_list")
        ids = []
        for item in page.data:
            model = getattr(item, "id", None)
            if (not isinstance(model, str) or not model or model != model.strip()
                    or len(model) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in model)):
                raise RequestFailure("invalid_model_list")
            if model not in ids:
                ids.append(model)
        return ids
    except RequestFailure:
        raise
    except Exception as exc:
        raise request_failure(exc) from None
    finally:
        client.close()


def select_model(path, model, module, config, listed):
    if model not in listed:
        raise ConfigError("Selected model is not in the current API model list; configuration was not changed.")
    if not accepts_model(module, model):
        raise ConfigError("Model is listed, but the current original imagegen CLI does not accept its name; configuration was not changed.")
    # Re-read just before writing so a concurrent local key edit is not overwritten.
    if read_config(path, require_key=True, require_model=False) != config:
        raise ConfigError("Configuration changed during discovery; retry selection with the current configuration.")
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
        values["model"] = model
        atomic_json(path, values)
    except (OSError, ValueError):
        raise ConfigError("Cannot save selected model; inspect the local config file.") from None


def print_safe(value, config):
    output = json.dumps(value, indent=2, ensure_ascii=True)
    if config.get("api_key"):
        output = output.replace(json.dumps(config["api_key"])[1:-1], "[REDACTED]")
    print(output)


def describe_models(config, listed, module, recorder):
    models = []
    for model in listed:
        status = recorder.status(model)
        models.append({"id": model, "listed": True,
                       "cli_accepts_name": accepts_model(module, model),
                       "generate": status["generate"]["status"],
                       "edit": status["edit"]["status"]})
    current_accepted = config["model"] in listed and accepts_model(module, config["model"])
    return {
        "configured_model": config["model"],
        "configured_model_is_listed": config["model"] in listed,
        "selection_required": not current_accepted,
        "models": models,
        "note": "Codex retains a listed CLI-accepted current model, or chooses an eligible candidate and saves it with models select ID. A user-requested model takes priority. Listing/selection does not verify image capability; no image request was made and no model was changed by discovery."
    }


def manage_models(arguments, config_path, state_path, module):
    parser = argparse.ArgumentParser(prog="run_imagegen.py models")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("list", help="Query /models only; makes no image request")
    select = commands.add_parser("select", help="Re-query /models and save an ID selected by Codex or the user")
    select.add_argument("model")
    commands.add_parser("status", help="Show local evidence for the current URL, key, and model")
    test = commands.add_parser("test", help="One real, potentially billable request for the selected model")
    test.add_argument("--confirm-billable", action="store_true")
    test.add_argument("--operation", choices=("generate", "edit"), default="generate")
    test.add_argument("--image", type=Path)
    test.add_argument("--out", type=Path, required=True)
    test.add_argument("--size", default="1024x1024")
    test.add_argument("--quality", default="low")
    args = parser.parse_args(arguments)
    config = read_config(config_path, require_key=True, require_model=args.action == "test")
    recorder = Recorder(config, state_path)
    if args.action == "status":
        print_safe(recorder.status(config["model"]), config)
    elif args.action in ("list", "select"):
        listed = fetch_models(config)
        if args.action == "select":
            select_model(config_path, args.model, module, config, listed)
            print_safe({"selected_model": args.model, "listed": True,
                        "capabilities_tested_by_selection": False,
                        "note": "Selection alone does not verify image capabilities; use models status for prior evidence."}, config)
        else:
            print_safe(describe_models(config, listed, module, recorder), config)
    else:
        if not args.confirm_billable:
            raise ConfigError("Testing makes a real, potentially billable image request. Add --confirm-billable only after explicit user approval.")
        if args.operation == "edit" and args.image is None:
            raise ConfigError("Editing verification requires --image PATH.")
        if args.operation == "generate" and args.image is not None:
            raise ConfigError("--image is only valid with --operation edit.")
        command = [args.operation, "--prompt", "A single blue ceramic cup on a plain white background.",
                   "--no-augment", "--n", "1", "--size", args.size, "--quality", args.quality,
                   "--out", str(args.out)]
        if args.operation == "edit":
            command[command.index("--prompt") + 1] = "Change only the cup color to green; preserve the composition and background."
            command += ["--image", str(args.image)]
        # Preflight output/input and CLI parameters without spending money.
        if args.out.exists():
            raise ConfigError("Test output already exists; choose a new --out path.")
        return run_original(module, config, command, recorder=recorder)
    return 0


def default_imagegen_dir() -> Path:
    sibling = SKILL_DIR.parent / ".system" / "imagegen"
    if sibling.is_dir():
        return sibling
    return Path.home() / ".codex" / "skills" / ".system" / "imagegen"


def load_original(skill_dir: Path) -> ModuleType:
    script = skill_dir / "scripts" / "image_gen.py"
    if not script.is_file() or not (skill_dir / "SKILL.md").is_file():
        raise ConfigError("Original imagegen skill not found; select it with --imagegen-dir.")
    spec = importlib.util.spec_from_file_location("_configured_original_imagegen", script)
    if spec is None or spec.loader is None:
        raise ConfigError("Cannot load the installed original imagegen CLI.")
    module = importlib.util.module_from_spec(spec)
    # Avoid writing __pycache__ into the original skill directory.
    old_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_bytecode
    required = {
        "main": (), "_ensure_api_key": ("dry_run",),
        "_create_client": (), "_create_async_client": (),
        "_run_generate_batch": ("args",),
        "_validate_model": ("model",),
        "_read_jobs_jsonl": ("path",),
    }
    for name, parameters in required.items():
        hook = getattr(module, name, None)
        if not callable(hook) or tuple(inspect.signature(hook).parameters) != parameters:
            raise ConfigError("Original imagegen CLI hooks changed; adapter update required.")
    return module


def run_original(module: ModuleType, config: dict[str, str], cli_args: list[str], *, recorder=None) -> int:
    sync_clients: list = []
    async_clients: list = []
    original_batch = module._run_generate_batch
    original_validate_model = module._validate_model
    listed = None
    selected_model = None
    old_hooks = {name: getattr(module, name) for name in (
        "_ensure_api_key", "_create_client", "_create_async_client", "_run_generate_batch", "_validate_model", "DEFAULT_MODEL"
    )}

    def validate_model(model):
        nonlocal selected_model
        if not model:
            raise ConfigError("No model selected. Run models list, then models select MODEL_ID.")
        original_validate_model(model)
        if listed is not None and model not in listed:
            raise ConfigError("Selected model is not in the current API list; run models list/select. No request was made for this model.")
        selected_model = model

    def check_key(dry_run: bool) -> None:
        nonlocal listed
        if not dry_run and not config["api_key"]:
            raise ConfigError("api_key is empty in the local config file.")
        if not dry_run:
            # Query once per invocation, after the original CLI's local checks.
            listed = fetch_models(config)
            validate_model(selected_model)

    def create_client(*, asynchronous: bool = False):
        try:
            import PIL.Image  # Check before sending a potentially billable request.
        except ImportError:
            raise ConfigError("Install Pillow before making an image request.") from None
        client = create_api_client(config, asynchronous=asynchronous)
        if asynchronous:
            async_clients.append(client)
        else:
            sync_clients.append(client)

        def finish(result, operation, payload):
            validate_images(result, int(payload.get("n", 1)))
            if recorder:
                recorder.record(operation, payload)
            return result

        def failed(exc, operation, payload):
            error = exc if isinstance(exc, RequestFailure) else request_failure(exc)
            if recorder:
                recorder.record(operation, payload, error)
            return error

        def wrap(operation):
            method = getattr(client.images, operation)
            if asynchronous:
                async def async_call(**payload):
                    validate_model(payload["model"])
                    try:
                        result = await method(**payload)
                        return finish(result, operation, payload)
                    except Exception as exc:
                        raise failed(exc, operation, payload) from None
                return async_call

            def sync_call(**payload):
                validate_model(payload["model"])
                try:
                    result = method(**payload)
                    return finish(result, operation, payload)
                except Exception as exc:
                    raise failed(exc, operation, payload) from None
            return sync_call

        return SimpleNamespace(images=SimpleNamespace(generate=wrap("generate"), edit=wrap("edit")))

    async def run_batch(args):
        try:
            # Check all per-job overrides before starting any billable job.
            for job in module._read_jobs_jsonl(args.input):
                validate_model(job.get("model") if job.get("model") is not None else args.model)
            return await original_batch(args)
        finally:
            # Close within the same event loop used by the original batch runner.
            for client in async_clients:
                await client.close()
            async_clients.clear()

    module._ensure_api_key = check_key
    module._create_client = create_client
    module._create_async_client = lambda: create_client(asynchronous=True)
    module._run_generate_batch = run_batch
    module._validate_model = validate_model
    module.DEFAULT_MODEL = config["model"]
    old_argv = sys.argv
    try:
        sys.argv = [str(module.__file__), *cli_args]
        return module.main()
    finally:
        sys.argv = old_argv
        for name, hook in old_hooks.items():
            setattr(module, name, hook)
        for client in sync_clients:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=SKILL_DIR / "config.json")
    parser.add_argument("--imagegen-dir", type=Path, default=default_imagegen_dir())
    parser.add_argument("--check-config", action="store_true", help="Validate locally; no network or secret output")
    parser.add_argument("--state", type=Path, help="Validation evidence path (default: beside the selected config)")
    parser.add_argument("imagegen_args", nargs=argparse.REMAINDER, help="Original command and arguments")
    args = parser.parse_args(argv)
    forwarded = args.imagegen_args
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    if not forwarded and not args.check_config:
        parser.error("provide generate, edit, generate-batch, or --check-config")
    if args.check_config and forwarded:
        parser.error("--check-config cannot be combined with an image command")
    config = None
    try:
        module = load_original(args.imagegen_dir.resolve())
        config_path = args.config.resolve()
        state_path = args.state.resolve() if args.state else config_path.with_name("validation-state.json")
        if state_path == config_path:
            raise ConfigError("--state must use a different file from --config.")
        if forwarded[:1] == ["models"]:
            if args.check_config:
                parser.error("--check-config cannot be combined with models")
            return manage_models(forwarded[1:], config_path, state_path, module)
        if "--help" in forwarded or "-h" in forwarded:
            config = {"api_key": "", "base_url": "", "model": ""}
        else:
            config = read_config(config_path, require_key=args.check_config or "--dry-run" not in forwarded,
                                 require_model=False)
        if args.check_config:
            if config["model"] and not accepts_model(module, config["model"]):
                raise ConfigError("Configured model name is not accepted by the original CLI. Run models list/select.")
            print("Config and original CLI hooks are valid. No network request was made. " +
                  ("Model selected; listing and capabilities are not checked." if config["model"] else
                   "No model selected; run models list, then models select MODEL_ID."))
            return 0
        recorder = Recorder(config, state_path)
        return run_original(module, config, forwarded, recorder=recorder)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Provider response bodies/URLs can echo credentials; never print them.
        message = str(exc) if isinstance(exc, RequestFailure) else "Operation failed; inspect local paths and configuration."
        print(f"Error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
