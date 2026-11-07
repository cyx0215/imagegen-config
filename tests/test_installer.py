import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('imagegen_installer', SOURCE / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_install_default_reinstall_and_remove_preserves_credentials(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            home = Path(temporary)
            original = '# Existing guidance\nKeep project edits focused.\n'
            agents = home / 'AGENTS.md'
            agents.write_text(original, encoding='utf-8')
            installer.main(['--codex-home', str(home), '--set-default'])
            config = home / 'skills/imagegen-config/config.json'
            values = json.loads(config.read_text())
            self.assertEqual(values['api_key'], '')  # Must never copy the real source key.
            values['api_key'] = 'test-local-only-secret'
            config.write_text(json.dumps(values), encoding='utf-8')
            state = config.with_name('validation-state.json')
            state.write_text('{"version": 1, "contexts": {}}', encoding='utf-8')
            before_config, before_state = config.read_bytes(), state.read_bytes()
            installer.main(['--codex-home', str(home), '--set-default'])
            self.assertEqual(json.loads(config.read_text())['api_key'], 'test-local-only-secret')
            self.assertEqual(config.read_bytes(), before_config)
            self.assertEqual(state.read_bytes(), before_state)
            self.assertTrue((config.parent / 'scripts/model_state.py').exists())
            text = agents.read_text(encoding='utf-8')
            self.assertTrue(text.startswith(original))
            self.assertEqual(text.count(installer.START), 1)
            self.assertNotIn('test-local-only-secret', text)
            installer.main(['--codex-home', str(home), '--unset-default'])
            self.assertNotIn(installer.START, agents.read_text(encoding='utf-8'))
            self.assertTrue(agents.read_text(encoding='utf-8').startswith(original))
            self.assertEqual(json.loads(config.read_text())['api_key'], 'test-local-only-secret')

    def test_nonempty_override_is_active_and_plain_install_does_not_change_routing(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            home = Path(temporary)
            agents, override = home/'AGENTS.md', home/'AGENTS.override.md'
            agents.write_text('base instructions', encoding='utf-8')
            override.write_text('temporary instructions', encoding='utf-8')
            installer.main(['--codex-home', str(home)])
            self.assertEqual(agents.read_text(), 'base instructions')
            self.assertEqual(override.read_text(), 'temporary instructions')
            installer.main(['--codex-home', str(home), '--set-default'])
            self.assertEqual(agents.read_text(), 'base instructions')
            self.assertIn(installer.START, override.read_text())
            installer.main(['--codex-home', str(home), '--unset-default'])
            self.assertTrue(override.read_text().startswith('temporary instructions'))

    def test_broken_markers_are_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            path = Path(temporary)/'AGENTS.md'
            text = 'user guidance\n' + installer.START
            path.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError):
                installer.update_rule(path, enabled=True)
            self.assertEqual(path.read_text(), text)


if __name__ == '__main__':
    unittest.main()
