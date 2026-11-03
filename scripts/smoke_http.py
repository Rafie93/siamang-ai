"""Real loopback HTTP smoke, always using a temporary DB, identity, and subprocess.

Run with the API-enabled interpreter: python scripts/smoke_http.py.
Does not use the operator's projects, model server, credentials, or database.
"""
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from siamang.projects import ProjectRegistry
from siamang.storage import Database


def main():
    with tempfile.TemporaryDirectory(prefix='siamang-http-smoke-') as tmp:
        root = Path(tmp)
        project_root = root / 'project'
        project_root.mkdir()
        with Database(root / 'db.sqlite') as db:
            project = ProjectRegistry(db).register(name='HTTP smoke', root_path=str(project_root))
        token = secrets.token_urlsafe(48)
        env = {k: v for k, v in os.environ.items() if not k.startswith('SIAMANG_')}
        env.update(SIAMANG_STORAGE_PATH=str(root / 'db.sqlite'), PYTHONDONTWRITEBYTECODE='1',
                   SIAMANG_API_SEAL_KEY=secrets.token_hex(32), SIAMANG_API_IDENTITIES=json.dumps({
                       'smoke': {'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
                                 'permissions': ['read', 'submit', 'run'], 'projects': [project.project_id]}}))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        log = open(root / 'server.log', 'w+')
        proc = subprocess.Popen([sys.executable, '-m', 'siamang.api.main', '--port', str(port)], env=env,
                                 stdout=log, stderr=log)
        base = f'http://127.0.0.1:{port}'

        def request(path, body=None, auth=True):
            headers = {'Authorization': 'Bearer ' + token} if auth else {}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(base + path, headers=headers,
                                          data=None if body is None else json.dumps(body).encode())
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                return exc.code, json.load(exc)

        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    status, health = request('/v0/health')
                    break
                except urllib.error.URLError:
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError('API did not start') from None
                    time.sleep(.05)
            assert status == 200 and health['status'] == 'healthy'
            assert request('/v0/health', auth=False)[0] == 401
            status, submitted = request('/v0/tasks', {'project_id': project.project_id,
                                       'description': 'Inspect this project.', 'request_id': 'req_smoke'})
            assert status == 202 and submitted['scheduled'] is False
            task_id = submitted['task']['task_id']
            assert request('/v0/tasks/' + task_id)[1]['status'] == 'queued'
            assert request('/v0/tasks/' + task_id + '/run', {})[0] == 503
            assert request('/v0/openapi.json')[0] == 200
            print('PASS: real loopback HTTP, authenticated reads, 401, durable submit, truthful queued state, '
                  '503 without model, OpenAPI. Temporary database only.')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.close()


if __name__ == '__main__':
    main()
