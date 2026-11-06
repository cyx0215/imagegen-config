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
from urllib.parse import urlsplit


SKILL_DIR = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    pass


def read_config(path: Path, *, require_key: bool) -> dict[str, str]:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        # JSON errors can contain excerpts of a secret-bearing file.
        raise ConfigError("Cannot read config as UTF-8 JSON; check the file locally.") from None
    if not isinstance(config, dict) or set(config) != {"base_url", "api_key", "model"}:
        raise ConfigError("Config must contain exactly base_url, api_key, and model.")
    for field, value in config.items():
        if not isinstance(value, str) or value != value.strip():
            raise ConfigError(f"Config field {field} must be a string without surrounding whitespace.")
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
    if not config["model"]:
        raise ConfigError("model must not be empty.")
    if require_key and not config["api_key"]:
        raise ConfigError("api_key is empty. Fill it in the local config file; do not paste it into chat.")
    return config


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
    }
    for name, parameters in required.items():
        hook = getattr(module, name, None)
        if not callable(hook) or tuple(inspect.signature(hook).parameters) != parameters:
            raise ConfigError("Original imagegen CLI hooks changed; adapter update required.")
    return module


def run_original(module: ModuleType, config: dict[str, str], cli_args: list[str]) -> int:
    sync_clients: list = []
    async_clients: list = []
    original_batch = module._run_generate_batch
    old_hooks = {name: getattr(module, name) for name in (
        "_ensure_api_key", "_create_client", "_create_async_client", "_run_generate_batch", "DEFAULT_MODEL"
    )}

    def check_key(dry_run: bool) -> None:
        if not dry_run and not config["api_key"]:
            raise ConfigError("api_key is empty in the local config file.")

    def create_client(*, asynchronous: bool = False):
        try:
            from openai import OpenAI, AsyncOpenAI, DefaultHttpxClient, DefaultAsyncHttpxClient
        except ImportError:
            raise ConfigError("Install the openai Python package in the active Python environment.") from None
        kwargs = dict(
            api_key=config["api_key"], base_url=config["base_url"],
            organization="", project="", webhook_secret="", max_retries=0,
        )
        if asynchronous:
            client = AsyncOpenAI(**kwargs, http_client=DefaultAsyncHttpxClient(trust_env=False))
            async_clients.append(client)
        else:
            client = OpenAI(**kwargs, http_client=DefaultHttpxClient(trust_env=False))
            sync_clients.append(client)
        return client

    async def run_batch(args):
        try:
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
        if "--help" in forwarded or "-h" in forwarded:
            config = {"api_key": "", "base_url": "", "model": module.DEFAULT_MODEL}
        else:
            config = read_config(args.config.resolve(), require_key=args.check_config or "--dry-run" not in forwarded)
        if args.check_config:
            module._validate_model(config["model"])
            print("Config and original CLI hooks are valid. No network request was made.")
            return 0
        return run_original(module, config, forwarded)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        message = str(exc)
        if config and config["api_key"]:
            message = message.replace(config["api_key"], "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
