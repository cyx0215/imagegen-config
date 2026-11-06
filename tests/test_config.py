import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('imagegen_adapter', ROOT/'scripts/run_imagegen.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class ConfigTests(unittest.TestCase):
    def load(self, values):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)/'config.json'
            config.write_text(json.dumps(values),encoding='utf-8')
            return adapter.read_config(config,require_key=True)

    def test_blank_or_missing_url_uses_explicit_official_default(self):
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://wrong.example/v1'}):
            for values in [{'api_key':'test'}, {'api_key':'test','base_url':''}]:
                with self.subTest(values=values):
                    config=self.load(values)
                    self.assertEqual(config['base_url'],'https://api.openai.com/v1')
                    self.assertEqual(config['model'],'gpt-image-2.6')

    def test_explicit_custom_values_are_preserved(self):
        values={'base_url':'https://your-provider.example/v1','api_key':'test','model':'gpt-image-2'}
        self.assertEqual(self.load(values),values)

    def test_blank_model_gets_default(self):
        self.assertEqual(self.load({'api_key':'test','model':''})['model'],'gpt-image-2.6')

    def test_invalid_nonempty_url_and_wrong_types_fail(self):
        for value in ['not-a-url','https://example.com/v1?token=abc',None,5]:
            with self.subTest(value=value), self.assertRaises(adapter.ConfigError):
                self.load({'api_key':'test','base_url':value})


if __name__ == '__main__':
    unittest.main()
