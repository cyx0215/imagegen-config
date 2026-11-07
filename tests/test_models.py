"""Behavioral integration tests; all requests stay on a local fake provider."""
import base64
import contextlib
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('model_adapter', ROOT / 'scripts/run_imagegen.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
buffer = io.BytesIO()
Image.new('RGB', (3, 3), 'blue').save(buffer, format='PNG')
PNG = buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.server.requests.append(('GET', self.path, dict(self.headers), None))
        self.send(self.server.list_status, self.server.model_payload)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        self.server.requests.append(('POST', self.path, dict(self.headers), raw))
        self.send(self.server.image_status, self.server.image_payload)


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.original = adapter.default_imagegen_dir() / 'scripts/image_gen.py'
        cls.original_hash = hashlib.sha256(cls.original.read_bytes()).digest()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        assert hashlib.sha256(cls.original.read_bytes()).digest() == cls.original_hash

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = self.folder / 'config.json'
        self.settings = {'base_url': f'http://127.0.0.1:{self.server.server_port}/v1',
                         'api_key': 'test-only-secret', 'model': 'gpt-image-test'}
        self.save()
        self.server.requests = []
        self.server.list_status = self.server.image_status = 200
        self.server.model_payload = {'object': 'list', 'data': [
            {'id': name, 'object': 'model'} for name in ('chat-model', 'gpt-image-test', 'gpt-image-other')]}
        self.server.image_payload = {'created': 1, 'data': [{'b64_json': base64.b64encode(PNG).decode()}]}

    def save(self):
        self.config.write_text(json.dumps(self.settings), encoding='utf-8')

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                result = adapter.main(['--config', str(self.config), *map(str, args)])
            except SystemExit as exc:
                result = exc.code
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(self.settings['api_key'], output)
        return result, output

    def status(self):
        code, output = self.invoke('models', 'status')
        self.assertEqual(code, 0, output)
        return json.loads(output)

    def generate(self, *extra):
        return self.invoke('generate', '--prompt', 'A private prompt, not to be stored in evidence',
                           '--out', self.folder / 'image.png', *extra)

    def test_listing_displays_all_models_without_post_or_mutation(self):
        before = self.config.read_bytes()
        code, output = self.invoke('models', 'list')
        self.assertEqual(code, 0, output)
        data = json.loads(output)
        self.assertTrue(data['configured_model_is_listed'])
        self.assertFalse(data['selection_required'])
        self.assertFalse(data['models'][0]['cli_accepts_name'])
        self.assertTrue(data['models'][1]['cli_accepts_name'])
        self.assertEqual(data['models'][1]['generate'], 'unverified')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual([r[:2] for r in self.server.requests], [('GET', '/v1/models')])
        self.assertFalse((self.folder / 'validation-state.json').exists())

    def test_selection_requires_current_list_membership_and_original_cli_name(self):
        before = self.config.read_bytes()
        for name in ('gpt-image-missing', 'chat-model'):
            self.assertEqual(self.invoke('models', 'select', name)[0], 2)
            self.assertEqual(self.config.read_bytes(), before)
        code, output = self.invoke('models', 'select', 'gpt-image-other')
        self.assertEqual(code, 0, output)
        selected = json.loads(self.config.read_text())
        self.assertEqual(selected, {**self.settings, 'model': 'gpt-image-other'})
        self.assertFalse(any(r[0] == 'POST' for r in self.server.requests))

    def test_no_fixed_default_model_or_paid_call_before_selection(self):
        del self.settings['model']
        self.save()
        self.assertEqual(self.invoke('models', 'list')[0], 0)
        self.server.requests.clear()
        self.assertEqual(self.generate()[0], 2)
        self.assertFalse(self.server.requests)
        self.assertEqual(self.invoke('--check-config')[0], 0)

    def test_success_records_decodable_image_and_keeps_edit_unverified(self):
        code, output = self.generate('--size', '1024x1024', '--quality', 'low')
        self.assertEqual(code, 0, output)
        self.assertEqual((self.folder / 'image.png').read_bytes(), PNG)
        status = self.status()
        self.assertEqual(status['generate']['status'], 'verified')
        self.assertEqual(status['edit']['status'], 'unverified')
        self.assertEqual(status['generate']['last_success']['parameters']['quality'], 'low')
        raw = (self.folder / 'validation-state.json').read_text()
        self.assertNotIn('test-only-secret', raw)
        self.assertNotIn('private prompt', raw)
        self.assertNotIn(self.settings['base_url'], raw)
        self.assertEqual([r[0] for r in self.server.requests], ['GET', 'POST'])

    def test_edit_has_separate_evidence_and_input_metadata(self):
        image = self.folder / 'input.png'
        image.write_bytes(PNG)
        code, output = self.invoke('edit', '--image', image, '--prompt', 'Green cup', '--out', self.folder / 'edit.png')
        self.assertEqual(code, 0, output)
        status = self.status()
        self.assertEqual(status['edit']['status'], 'verified')
        self.assertEqual(status['generate']['status'], 'unverified')
        self.assertEqual(status['edit']['last_success']['parameters']['input_image_count'], 1)

    def test_url_key_or_model_change_invalidates_current_evidence(self):
        self.assertEqual(self.generate()[0], 0)
        for field, value in [('api_key', 'different-key'), ('base_url', self.settings['base_url'] + '/other'),
                             ('model', 'gpt-image-other')]:
            original = self.settings[field]
            self.settings[field] = value
            self.save()
            self.assertEqual(self.status()['generate']['status'], 'unverified')
            self.settings[field] = original
        self.save()
        self.assertEqual(self.status()['generate']['status'], 'verified')

    def test_429_is_temporary_and_response_body_does_not_leak_key(self):
        self.server.image_status = 429
        self.server.image_payload = {'error': {'message': 'echo test-only-secret', 'type': 'rate_limit'}}
        code, output = self.generate()
        self.assertEqual(code, 1)
        self.assertIn('rate_limited', output)
        status = self.status()['generate']
        self.assertEqual(status['status'], 'unverified')
        self.assertEqual(status['last_attempt']['http_status'], 429)

    def test_temporary_failure_preserves_success_history(self):
        self.assertEqual(self.generate()[0], 0)
        self.server.image_status = 503
        self.assertEqual(self.generate('--force')[0], 1)
        status = self.status()['generate']
        self.assertEqual(status['status'], 'verified')
        self.assertEqual(status['last_attempt']['result'], 'service_error')
        self.assertEqual(status['last_success']['result'], 'verified')

    def test_invalid_images_are_never_recorded_as_verified_or_written(self):
        for data in ([{'b64_json': '!!!'}], [{'b64_json': base64.b64encode(b'not an image').decode()}],
                     [{'url': 'https://example.invalid/image.png'}], []):
            with self.subTest(data=data):
                self.server.image_payload = {'created': 1, 'data': data}
                code, output = self.generate()
                self.assertEqual(code, 1, output)
                self.assertEqual(self.status()['generate']['status'], 'unverified')
                self.assertEqual(self.status()['generate']['last_attempt']['result'], 'invalid_image_response')
                self.assertFalse((self.folder / 'image.png').exists())

    def test_unlisted_model_or_failed_discovery_stops_before_post(self):
        self.settings['model'] = 'gpt-image-missing'
        self.save()
        self.assertEqual(self.generate()[0], 2)
        self.server.list_status = 401
        self.server.model_payload = {'error': {'message': 'echo test-only-secret'}}
        self.assertEqual(self.generate()[0], 1)
        self.assertFalse(any(r[0] == 'POST' for r in self.server.requests))

    def test_dry_run_and_help_make_no_requests_or_evidence(self):
        self.settings['api_key'] = ''
        self.save()
        # invoke() checks non-disclosure only for a nonempty key.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(adapter.main(['--config', str(self.config), 'generate', '--prompt', 'Cup', '--dry-run']), 0)
        self.settings['api_key'] = 'test-only-secret'
        self.save()
        self.assertEqual(self.generate('--dry-run')[0], 0)
        self.assertEqual(self.invoke('models', 'test', '--out', self.folder / 'test.png')[0], 2)
        self.assertFalse(self.server.requests)
        self.assertFalse((self.folder / 'validation-state.json').exists())

    def test_explicit_test_runs_only_one_request_and_checks_output_path(self):
        path = self.folder / 'test.png'
        code, output = self.invoke('models', 'test', '--confirm-billable', '--out', path)
        self.assertEqual(code, 0, output)
        self.assertEqual([r[0] for r in self.server.requests], ['GET', 'POST'])
        before = len(self.server.requests)
        self.assertEqual(self.invoke('models', 'test', '--confirm-billable', '--out', path)[0], 2)
        self.assertEqual(len(self.server.requests), before)

    def test_batch_checks_all_model_overrides_before_any_paid_request(self):
        jobs = self.folder / 'jobs.jsonl'
        jobs.write_text(json.dumps({'prompt': 'Cup'}) + '\n' + json.dumps({'prompt': 'Mug', 'model': 'gpt-image-missing'}))
        code, output = self.invoke('generate-batch', '--input', jobs, '--out-dir', self.folder / 'batch')
        self.assertEqual(code, 2, output)
        self.assertFalse(any(r[0] == 'POST' for r in self.server.requests))

    def test_batch_records_per_job_model_and_async_failures(self):
        jobs = self.folder / 'jobs.jsonl'
        jobs.write_text(json.dumps({'prompt': 'Cup'}) + '\n' + json.dumps({'prompt': 'Mug', 'model': 'gpt-image-other'}))
        code, output = self.invoke('generate-batch', '--input', jobs, '--out-dir', self.folder / 'batch', '--max-attempts', '1')
        self.assertEqual(code, 0, output)
        recorder = adapter.Recorder(self.settings, self.folder / 'validation-state.json')
        self.assertEqual(recorder.status('gpt-image-other')['generate']['status'], 'verified')
        self.assertEqual(recorder.status('gpt-image-test')['generate']['status'], 'verified')
        self.server.image_status = 429
        code, output = self.invoke('generate-batch', '--input', jobs, '--out-dir', self.folder / 'failed', '--max-attempts', '1')
        self.assertEqual(code, 1, output)
        self.assertEqual(recorder.status('gpt-image-other')['generate']['last_attempt']['result'], 'rate_limited')

    def test_bad_model_list_does_not_change_config(self):
        before = self.config.read_bytes()
        self.server.model_payload = {'data': [{'id': 'bad\nmodel'}]}
        self.assertEqual(self.invoke('models', 'select', 'gpt-image-test')[0], 1)
        self.assertEqual(self.config.read_bytes(), before)

    def test_interactive_config_only_requires_url_and_key(self):
        del self.settings['model']
        self.save()
        output = io.StringIO()
        with patch('builtins.input', side_effect=['']) as url_input, patch('getpass.getpass', return_value='new-local-key') as key_input, contextlib.redirect_stdout(output):
            adapter.configure_interactively(self.config)
        self.assertEqual(url_input.call_count, 1)  # No model ID/number prompt.
        self.assertEqual(key_input.call_count, 1)
        self.assertNotIn('new-local-key', output.getvalue())
        values = json.loads(self.config.read_text())
        self.assertEqual(values['api_key'], 'new-local-key')
        self.assertEqual(values['model'], '')  # Codex decides; installer never guesses.
        self.assertEqual([r[0] for r in self.server.requests], ['GET'])
        report = adapter.describe_models(values, ['chat-model', 'gpt-image-other'],
                                         adapter.load_original(adapter.default_imagegen_dir()),
                                         adapter.Recorder(values, self.folder / 'validation-state.json'))
        self.assertTrue(report['selection_required'])
        self.assertFalse(report['models'][0]['cli_accepts_name'])

    def test_reconfigure_keeps_existing_model_without_a_model_prompt(self):
        with patch('builtins.input', side_effect=['']) as url_input, patch('getpass.getpass', return_value=''), contextlib.redirect_stdout(io.StringIO()):
            adapter.configure_interactively(self.config)
        self.assertEqual(url_input.call_count, 1)
        self.assertEqual(json.loads(self.config.read_text()), self.settings)
        self.assertEqual([r[0] for r in self.server.requests], ['GET'])

    def test_listing_requests_agent_selection_for_missing_or_unavailable_model(self):
        for model in ('', 'gpt-image-gone', 'chat-model'):
            with self.subTest(model=model):
                self.settings['model'] = model
                self.save()
                code, output = self.invoke('models', 'list')
                self.assertEqual(code, 0, output)
                report = json.loads(output)
                self.assertTrue(report['selection_required'])
                self.assertEqual(json.loads(self.config.read_text())['model'], model)

    def test_fresh_setup_agent_choice_then_user_change_persists_actual_model(self):
        del self.settings['model']
        self.save()
        with patch('builtins.input', side_effect=['']), patch('getpass.getpass', return_value=''), contextlib.redirect_stdout(io.StringIO()):
            adapter.configure_interactively(self.config)
        # Codex makes the choice outside the installer, then uses this API.
        self.assertEqual(self.invoke('models', 'select', 'gpt-image-other')[0], 0)
        code, output = self.generate()
        self.assertEqual(code, 0, output)
        posted = [r for r in self.server.requests if r[0] == 'POST']
        self.assertEqual(json.loads(posted[-1][3])['model'], 'gpt-image-other')
        self.assertEqual(json.loads(self.config.read_text())['model'], 'gpt-image-other')
        # A later change is persisted for subsequent calls, not just one override.
        self.assertEqual(self.invoke('models', 'select', 'gpt-image-test')[0], 0)
        self.assertEqual(self.generate('--force')[0], 0)
        posted = [r for r in self.server.requests if r[0] == 'POST']
        self.assertEqual(json.loads(posted[-1][3])['model'], 'gpt-image-test')
        self.assertEqual(json.loads(self.config.read_text())['model'], 'gpt-image-test')

    def test_atomic_evidence_updates_do_not_lose_concurrent_records(self):
        recorder = adapter.Recorder(self.settings, self.folder / 'validation-state.json')
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: recorder.record('generate', {'model': 'gpt-image-test', 'n': 1}), range(20)))
        state = json.loads((self.folder / 'validation-state.json').read_text())
        self.assertEqual(len(next(iter(state['contexts'].values()))), 20)

    def test_state_write_failure_does_not_retry_or_fail_an_image_request(self):
        with patch('model_state.atomic_json', side_effect=OSError('local state blocked')):
            code, output = self.generate()
        self.assertEqual(code, 0, output)
        self.assertIn('could not be saved', output)
        self.assertEqual(len([r for r in self.server.requests if r[0] == 'POST']), 1)
        self.assertTrue((self.folder / 'image.png').exists())

    def test_request_rejection_does_not_claim_model_is_unsupported(self):
        self.server.image_status = 400
        self.server.image_payload = {'error': {'message': 'Parameter issue, echo test-only-secret'}}
        code, output = self.generate()
        self.assertEqual(code, 1, output)
        self.assertEqual(self.status()['generate']['last_attempt']['result'], 'request_rejected')
        self.assertEqual(self.status()['generate']['status'], 'unverified')

    def test_conflicting_environment_does_not_affect_model_discovery(self):
        import os
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'wrong-key', 'OPENAI_BASE_URL': 'http://127.0.0.1:1',
                                     'HTTP_PROXY': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1',
                                     'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
            before = dict(os.environ)
            code, output = self.invoke('models', 'list')
            self.assertEqual(code, 0, output)
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(self.server.requests[0][2]['Authorization'], 'Bearer test-only-secret')

    def test_long_failure_history_keeps_prior_success(self):
        recorder = adapter.Recorder(self.settings, self.folder / 'validation-state.json')
        recorder.record('generate', {'model': 'gpt-image-test'})
        for _ in range(105):
            recorder.record('generate', {'model': 'gpt-image-test'}, adapter.RequestFailure('service_error', 503))
        status = recorder.status('gpt-image-test')['generate']
        self.assertEqual(status['status'], 'verified')
        self.assertEqual(status['last_attempt']['http_status'], 503)

    def test_local_validation_and_invalid_paid_test_options_make_no_requests(self):
        self.assertEqual(self.invoke('models', 'test', '--confirm-billable', '--size', 'wrong',
                                     '--out', self.folder / 'test.png')[0], 1)
        self.assertFalse(self.server.requests)
        self.assertEqual(self.invoke('models', 'test', '--confirm-billable', '--operation', 'edit',
                                     '--out', self.folder / 'test.png')[0], 2)
        self.assertFalse(self.server.requests)


if __name__ == '__main__':
    unittest.main()
