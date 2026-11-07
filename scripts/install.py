#!/usr/bin/env python3
"""Install imagegen-config and optionally record the user's default image route."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys

SOURCE = Path(__file__).resolve().parent.parent
START = '<!-- imagegen-config:default:start -->'
END = '<!-- imagegen-config:default:end -->'
RULE = '''## Default image generation preference

For ordinary raster image generation and image edits, use the installed `imagegen-config` skill by default, even when the user does not name it. Load that skill before the original `imagegen` skill. This is the user's standing choice of the configured CLI/API route. Follow an explicit user request for another skill or provider instead.

Read the URL, key, and model only through the skill's config adapter. Do not ask for OPENAI_API_KEY, set environment variables, modify the original imagegen skill, or silently switch to the built-in image tool. If config is missing or incomplete, explain which local configuration is needed without displaying secrets. Keep the original imagegen prompt, editing, and output-review workflow through the adapter. Code-native SVG/vector edits remain outside this image route.
'''


def global_file(home: Path) -> Path:
    override = home / 'AGENTS.override.md'
    if override.is_file() and override.read_text(encoding='utf-8-sig').strip():
        return override
    return home / 'AGENTS.md'


def update_rule(path: Path, *, enabled: bool) -> bool:
    existing = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    start, end = existing.find(START), existing.find(END)
    if existing.count(START) > 1 or existing.count(END) > 1 or (start == -1) != (end == -1) or (end != -1 and end < start):
        raise ValueError('Managed default-route markers are malformed; resolve the AGENTS file locally.')
    block = f'{START}\n{RULE.rstrip()}\n{END}'
    if start != -1:
        updated = existing[:start] + (block if enabled else '') + existing[end+len(END):]
    elif enabled:
        updated = existing + ('\n\n' if existing and not existing.endswith('\n\n') else '') + block + '\n'
    else:
        return False
    if updated == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding='utf-8')
    return True


def configure(path: Path) -> None:
    # All credential access and API discovery go through the config adapter.
    from run_imagegen import configure_interactively
    configure_interactively(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home()/'.codex')))
    parser.add_argument('--configure', action='store_true', help='Prompt for URL/hidden key only; discover models for Codex to select')
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument('--set-default', action='store_true', help='Add the default image route to global AGENTS')
    choices.add_argument('--unset-default', action='store_true', help='Remove only the managed default route; leave the skill and credentials intact')
    args = parser.parse_args(argv)
    home = args.codex_home.expanduser().resolve()
    if args.unset_default:
        # A global override can have been created after installation. Remove our
        # markers from both possible global files so they cannot reappear later.
        for filename in ('AGENTS.md', 'AGENTS.override.md'):
            path = home / filename
            if path.exists():
                update_rule(path, enabled=False)
        print('Default image route removed. Restart the chat to reload instructions.')
        return 0
    target = home / 'skills' / 'imagegen-config'
    target.mkdir(parents=True, exist_ok=True)
    # Explicit allowlist: never copy a credential-bearing config.json from source.
    for relative in ('SKILL.md', 'agents/openai.yaml', 'config.example.json', 'scripts/run_imagegen.py', 'scripts/model_state.py', 'scripts/install.py'):
        source = SOURCE / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
    config = target / 'config.json'
    if not config.exists():
        shutil.copyfile(target / 'config.example.json', config)
        if os.name != 'nt':
            config.chmod(0o600)
    if args.configure:
        sys.path.insert(0, str(SOURCE / 'scripts'))
        configure(config)
    if args.set_default:
        update_rule(global_file(home), enabled=True)
        print('Default image route recorded. Start a new chat to load it.')
    print(f'Skill installed: {target}')
    print(f'Local config: {config} (existing credentials preserved)')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, EOFError) as exc:
        # Do not echo malformed configuration excerpts or credentials.
        print(f'Installation failed ({type(exc).__name__}); check local paths/configuration.', file=sys.stderr)
        raise SystemExit(2)
