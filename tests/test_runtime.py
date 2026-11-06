"""Integration checks against a local fake Images API; no external requests."""
import base64
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = Path.home() / '.codex/skills/.system/imagegen/scripts/image_gen.py'
spec = importlib.util.spec_from_file_location('config_adapter', ROOT / 'scripts/run_imagegen.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB9sAAAAASUVORK5CYII=')
requests = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        requests.append((self.path, dict(self.headers), body))
        payload = json.dumps({'created': 1, 'data': [{'b64_json': base64.b64encode(PNG).decode()}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def invoke(config, *args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = adapter.main(['--config', str(config), *map(str, args)])
    output = stdout.getvalue() + stderr.getvalue()
    assert 'local-test-key' not in output
    return result, output


def main():
    before_hash = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            folder = Path(temp)
            config = folder / 'config.json'
            settings = {'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'local-test-key', 'model': 'gpt-image-2.6'}
            config.write_text(json.dumps(settings), encoding='utf-8')
            poisoned_env = {
                'OPENAI_API_KEY': 'wrong-environment-key',
                'OPENAI_BASE_URL': 'http://127.0.0.1:1/never-use',
                'OPENAI_ORG_ID': 'wrong-org', 'OPENAI_PROJECT_ID': 'wrong-project',
                'HTTP_PROXY': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1',
                'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': '',
            }
            with patch.dict(os.environ, poisoned_env):
                snapshot = dict(os.environ)
                assert invoke(config, '--check-config')[0] == 0
                assert not requests
                assert invoke(config, 'generate', '--prompt', 'A mug', '--dry-run')[0] == 0
                assert not requests
                assert invoke(config, 'generate', '--prompt', 'A mug', '--out', folder / 'generated.png')[0] == 0
                assert (folder / 'generated.png').read_bytes() == PNG
                image = folder / 'input.png'
                image.write_bytes(PNG)
                assert invoke(config, 'edit', '--image', image, '--prompt', 'Replace background', '--out', folder / 'edited.png')[0] == 0
                assert (folder / 'edited.png').read_bytes() == PNG
                jobs = folder / 'jobs.jsonl'
                jobs.write_text(json.dumps({'prompt': 'A blue mug'}) + '\n' + json.dumps({'prompt': 'A red mug'}) + '\n', encoding='utf-8')
                assert invoke(config, 'generate-batch', '--input', jobs, '--out-dir', folder / 'batch', '--max-attempts', '1')[0] == 0
                assert len(list((folder / 'batch').glob('*.png'))) == 2
                assert dict(os.environ) == snapshot, 'Adapter changed the environment'
            assert len(requests) == 4
            for path, headers, body in requests:
                assert path in {'/v1/images/generations', '/v1/images/edits'}
                assert headers['Authorization'] == 'Bearer local-test-key'
                assert not headers.get('OpenAI-Organization')
                assert not headers.get('OpenAI-Project')
                assert b'gpt-image-2.6' in body
                if path.endswith('/generations'):
                    assert json.loads(body)['model'] == 'gpt-image-2.6'
            settings['api_key'] = ''
            config.write_text(json.dumps(settings), encoding='utf-8')
            assert invoke(config, 'generate', '--prompt', 'A mug')[0] == 2
            assert invoke(config, 'generate', '--prompt', 'A mug', '--dry-run')[0] == 0
            assert len(requests) == 4
            config.write_text('{"api_key": "local-test-key" BROKEN}', encoding='utf-8')
            assert invoke(config, '--check-config')[0] == 2
            assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == before_hash
            print('PASS: generate, edit, async batch, config-only credentials, conflicting environment isolation, no environment mutation, blank key/dry run, secret-safe JSON error, original file unchanged.')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
