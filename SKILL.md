---
name: imagegen-config
description: "Generate and edit raster images using the user's configured image API. Use for ordinary image requests such as generate an image, draw a cat, edit this photo, 生图, 画一张图, or 给图片加帽子, when imagegen-config is the user's default; also use when explicitly requested. Reuse original imagegen with local config-file credentials and no environment changes. Exclude code-native SVG and vector edits."
---

# Imagegen Config

Use the original `imagegen` workflow with credentials from this skill's `config.json`. This skill selects the original CLI/API mode explicitly; do not use the built-in image tool for these requests.

## Default routing

When the user's global or project instructions select `imagegen-config` as the default, apply it to ordinary raster image generation and edits without requiring the user to repeat its name. That standing preference explicitly selects the configured CLI/API path, so do not ask for `OPENAI_API_KEY` or seek repeated permission merely because the original skill defaults to a built-in tool. A user's explicit choice of another skill or provider takes precedence.

Automatic discovery is enabled. A description alone does not establish priority over other image skills. The repository's installer offers `--set-default` to record the user's preference in their active global AGENTS file, and `--unset-default` to remove only its managed block. Tell users to start a new chat after installation so the new instructions are loaded. Do not claim that installing the skill alone guarantees default selection.

## Configuration

- Read configuration only through `scripts/run_imagegen.py`; never display the contents of `config.json` or its API key in chat, shell output, or command arguments.
- `config.json` beside this file contains `api_key` and optional `base_url` and `model`. A blank or omitted URL uses `https://api.openai.com/v1` explicitly, never an inherited environment setting. The default model is `gpt-image-2.6` when omitted or blank. The key is initially blank; the user fills it locally. Do not ask them to paste a key into chat.
- A nonempty base URL must match the provider's OpenAI-compatible Images API prefix (commonly `/v1`). The default model is the user's selected provider alias, not a claim of official OpenAI availability; users of the official endpoint may need to choose a supported model. Model aliases and supported features are provider-specific; verify them with an actual response.
- To change the URL or model, edit only the requested fields and preserve the key. Do not place a key in `SKILL.md` or copy a populated config into deliverables.
- Do not set process, user, or system environment variables, including `OPENAI_*`. Do not read credentials from Codex's global configuration or other projects.

## Workflow

1. Locate and read the original `imagegen` skill using its actual catalog path, or the sibling `../.system/imagegen/SKILL.md`. Reuse its prompt preparation, generate/edit selection, constraints, output inspection, and iteration guidance. For this explicit API path, read its CLI reference as needed. If installed elsewhere, pass that original skill's directory with `--imagegen-dir` before the subcommand.
2. Use this skill's persistent adapter instead of invoking the original script directly. It imports the installed original CLI without changing its files, supplies clients with explicit config values, replaces its environment-only credential check in memory, and delegates execution to its original entry point. These limited runtime bindings are intentional. If the expected hooks change, stop and report the incompatibility; do not modify the original.
3. Run `--check-config` before the first live request. A missing key requires local configuration; do not fall back to another provider or the built-in tool. A dry run validates the original CLI payload without making a request and permits an empty key.
4. Forward the user's image parameters to the original CLI. The config model becomes its default; use `--model` only for an explicit user-selected override. Preserve the original model and parameter validation; do not silently alias `gpt-image-2.6` to another model or claim it has verified capabilities.
5. Use an explicit `--out` or `--out-dir` in the task's deliverables folder. Respect the active workspace's output conventions. Inspect the resulting images and report their paths, the final prompt, and that the config adapter used the original imagegen CLI.

## Commands

Resolve this skill directory from this file's actual location; do not assume any particular username. For example, from PowerShell at the skill directory:

```powershell
$runner = Join-Path (Get-Location) 'scripts/run_imagegen.py'
python $runner --check-config
python $runner generate --prompt 'A ceramic mug on a stone table' --size 1024x1024 --out 'outputs/mug.png' --dry-run
python $runner generate --prompt-file 'work/prompt.txt' --size 1024x1024 --out 'outputs/mug.png'
python $runner edit --image 'work/input.png' --prompt 'Replace only the background' --out 'outputs/edited.png'
python $runner generate-batch --input 'work/jobs.jsonl' --out-dir 'outputs/images'
```

An alternate config may be selected with `--config PATH` before the subcommand. `--imagegen-dir PATH` selects an original skill installed elsewhere. The adapter forwards all arguments after the subcommand unchanged. `--help` describes the adapter; `generate --help` describes the original command.

The active Python must have `openai` installed; Pillow is needed for optional resizing. The adapter does not write environment variables. It explicitly supplies API URL, key, and empty organization/project values, and disables inherited HTTP proxy settings for its own HTTP clients. Network access goes directly to the configured provider. The original script still handles image requests and responses; the provider must support the OpenAI Images request format and `b64_json` image data. A config check validates fields and CLI hooks locally; it does not establish network access or provider compatibility.
