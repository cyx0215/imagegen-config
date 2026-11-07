---
name: imagegen-config
description: "Generate and edit raster images using the user's configured image API; discover/select its models and inspect generation/edit verification evidence. Use for ordinary image requests such as generate an image, draw a cat, edit this photo, 生图, 画一张图, or 给图片加帽子, when imagegen-config is the user's default; also use when explicitly requested. Reuse original imagegen with local config-file credentials and no environment changes. Exclude code-native SVG and vector edits."
---

# Imagegen Config

Use the original `imagegen` workflow with credentials from this skill's `config.json`. This skill selects the original CLI/API mode explicitly; do not use the built-in image tool for these requests.

## Default routing

When the user's global or project instructions select `imagegen-config` as the default, apply it to ordinary raster image generation and edits without requiring the user to repeat its name. That standing preference explicitly selects the configured CLI/API path, so do not ask for `OPENAI_API_KEY` or seek repeated permission merely because the original skill defaults to a built-in tool. A user's explicit choice of another skill or provider takes precedence.

Automatic discovery is enabled. A description alone does not establish priority over other image skills. The repository's installer offers `--set-default` to record the user's preference in their active global AGENTS file, and `--unset-default` to remove only its managed block. Tell users to start a new chat after installation so the new instructions are loaded. Do not claim that installing the skill alone guarantees default selection.

## Configuration

- Read configuration only through `scripts/run_imagegen.py`; never display the contents of `config.json` or its API key in chat, shell output, or command arguments.
- `config.json` beside this file contains `api_key`, optional `base_url`, and a `model` selected by Codex or the user. A blank or omitted URL uses `https://api.openai.com/v1` explicitly, never an inherited environment setting. There is **no fixed default model**. Users only need to supply URL/key; Codex discovers and saves a model before image requests. A blank model permits discovery/configuration but blocks direct image CLI requests until selection is completed. The key is initially blank; users fill it locally. Do not ask them to paste a key into chat.
- A nonempty base URL must match the provider's OpenAI-compatible Images API prefix (commonly `/v1`). Model aliases and supported features are provider-specific. The API must expose an OpenAI-compatible `/models` list as well as Images endpoints for this workflow.
- To change the URL or model, edit only the requested fields and preserve the key. Do not place a key in `SKILL.md` or copy a populated config into deliverables.
- Do not set process, user, or system environment variables, including `OPENAI_*`. Do not read credentials from Codex's global configuration or other projects.

## Workflow

1. Locate and read the original `imagegen` skill using its actual catalog path, or the sibling `../.system/imagegen/SKILL.md`. Reuse its prompt preparation, generate/edit selection, constraints, output inspection, and iteration guidance. For this explicit API path, read its CLI reference as needed. If installed elsewhere, pass that original skill's directory with `--imagegen-dir` before the subcommand.
2. Use this skill's persistent adapter instead of invoking the original script directly. It imports the installed original CLI without changing its files, supplies clients with explicit config values, replaces its environment-only credential check in memory, and delegates execution to its original entry point. These limited runtime bindings are intentional. If the expected hooks change, stop and report the incompatibility; do not modify the original.
3. Run `--check-config` before the first live request. It checks local fields/hooks and reports a missing model without choosing one. A missing key requires local configuration; do not fall back to another provider or the built-in tool. A dry run validates the original CLI payload without making a request and permits an empty key, but still requires an explicitly selected model.
4. Use `models list` to discover IDs from the configured API. Follow **Model selection** below: choose and save a model yourself when needed, without requiring the user to supply a model ID. Distinguish `cli_accepts_name` from actual capabilities. `models select MODEL_ID` refreshes the list, validates the chosen ID, and saves it while preserving the URL/key.
5. Forward image parameters to the original CLI. The config model becomes its default; use `--model` only for an explicit user-selected override. Original model/parameter validators are preserved. Before each real invocation, the adapter refreshes `/models` and checks all selected models (including batch overrides) before any image request. A failed list query or disappeared model stops the invocation; do not silently use stale results.
6. Use an explicit `--out` or `--out-dir` in the task's deliverables folder. Respect the active workspace's output conventions. Inspect the resulting images and report their paths, the final prompt, and that the config adapter used the original imagegen CLI.

## Model selection

Users configure URL and key; Codex handles model discovery, choice, and persistence. During setup, complete selection as soon as credentials are available. If setup was run in a terminal without Codex, complete it on the first image request. A missing model or `selection_required: true` is an instruction to continue discovery/selection, not a reason to ask the user to fill a model field.

- A model the user names takes priority. Confirm it is listed and accepted by the CLI, then save it with `models select ID` when the user requests a persistent choice. A one-request override may use `--model`. If the explicitly requested ID is unavailable, explain that instead of substituting another ID.
- If there is no new model preference and the configured model remains listed and accepted, retain it. This includes a valid model the user manually wrote into config.
- For a fresh/missing/unavailable default, choose from the listed, CLI-accepted candidates. Consider the requested operation/parameters, prior successful evidence for that operation, and any reliable provider capability information. When evidence does not distinguish candidates, choose a reasonable unverified starting candidate and say it will be verified during normal use. Do not claim unknown quality, cost, availability, or feature superiority, or infer a ranking just from list order/model-name suffixes. Do not hardcode an ID or require the user to choose merely because several candidates exist.
- Persist the decision with `models select ID` before proceeding and briefly state which model was saved. If the user says “换个模型” without naming one, select a different eligible candidate and save it. If they name one, use that exact listed ID. They can also change `model` locally; preserve a valid manual choice.
- If discovery fails, no compatible candidate exists, or explicit user constraints cannot be met, explain the concrete blocker and retain configuration. A failed image request alone does not justify repeatedly changing models or launching paid probes.

The installer collects URL and hidden key only, then prints discovery results for Codex. It does not invoke a model agent itself or pick the first API entry. Pure-terminal installation leaves a fresh model blank until Codex completes selection during setup/first use; the user does not need to enter a model.

## Capability evidence

Normal image requests automatically provide evidence; do not make additional paid probes by default. `models status` reads local evidence for the current URL, key, and model. Generation and editing are separate: success in one never proves the other.

A request is recorded as `verified` only when all requested outputs contain base64 data that decode into valid PNG/JPEG/WebP images. Invalid data/URL-only responses are not verified. This checks image-data compatibility, not visual quality or prompt adherence; inspect outputs as usual. Verification applies only to the recorded operation, parameters, credentials, and time. HTTP errors, timeouts and rate limits are recorded by category, never as definitive proof that a model lacks image capability. A later failure is visible beside any prior success.

`validation-state.json` beside the selected config stores timestamps, operation, parameters and outcomes. It contains neither raw credentials nor prompts/input paths, and is excluded from releases. Its one-way context identifier includes URL, key, and model, so changing any of those yields an unverified scope. A dry run, help, listing, selection or local config check never records an image success. A state-write warning must not cause a potentially billable request to repeat.

Only when the user explicitly requests a paid test, use `models test --confirm-billable --out PATH`. This performs one generation request for the selected model. Editing requires `--operation edit --image PATH` and separate user intent. Never test every listed model. Explain that this is a real, potentially billable request before using the confirmation flag.

## Commands

Resolve this skill directory from this file's actual location; do not assume any particular username. For example, from PowerShell at the skill directory:

```powershell
$runner = Join-Path (Get-Location) 'scripts/run_imagegen.py'
python $runner --check-config
python $runner models list
python $runner models select MODEL_ID_FROM_THE_LIST
python $runner models status
python $runner generate --prompt 'A ceramic mug on a stone table' --size 1024x1024 --out 'outputs/mug.png' --dry-run
python $runner generate --prompt-file 'work/prompt.txt' --size 1024x1024 --out 'outputs/mug.png'
python $runner edit --image 'work/input.png' --prompt 'Replace only the background' --out 'outputs/edited.png'
python $runner generate-batch --input 'work/jobs.jsonl' --out-dir 'outputs/images'
```

An alternate config may be selected with `--config PATH` before the subcommand; its evidence is stored beside it unless `--state PATH` is specified. `--imagegen-dir PATH` selects an original skill installed elsewhere. Image arguments are forwarded unchanged. `--help` describes the adapter, `models --help` describes model management, and `generate --help` describes the original command. `install.py --configure` asks only for URL and hidden key, then queries models; Codex completes any required model selection.

The active Python must have `openai` and Pillow installed; Pillow verifies returned images before success is recorded. The adapter does not write environment variables. It explicitly supplies API URL, key, and empty organization/project values, and disables inherited HTTP proxy settings for its own HTTP clients. Network access goes directly to the configured provider. The original script still handles image requests and responses; the provider must support the OpenAI Images request format and `b64_json` image data. A config check validates fields and CLI hooks locally; it does not establish network access or provider compatibility.
