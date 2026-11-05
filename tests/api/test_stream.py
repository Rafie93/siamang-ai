"""The event stream, over a real uvicorn: TestClient buffers a whole response, so it cannot show what arrives when."""
import contextlib
import hashlib
import http.client
import json
import os
import queue
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytest.importorskip('fastapi')
httpx = pytest.importorskip('httpx')
uvicorn = pytest.importorskip('uvicorn')

from siamang.api.app import create_app
from siamang.api.settings import APISettings, Principal
from siamang.api.store import CHANGE_RETENTION, WATCHED_WRITES, RunStore
from siamang.api.stream import ChangeHub, follow, frame
from siamang.projects import ProjectRegistry
from siamang.storage import Database
from siamang.tasks import TaskEngine, TaskRequest
from tests.support.analyst import sample_project
from tests.support.loop import CLEAN
from tests.support.programmer import INSTRUCTION, make_model

_connect, _create_connection = socket.socket.connect, socket.create_connection  # before the suite's offline guard

TOKENS = {'operator': 'stream-operator-credential-' + 'a' * 32, 'other': 'stream-other-credential-' + 'b' * 32,
          'nothing': 'stream-nothing-credential-' + 'c' * 32}
# Tables that hold no state a reader can see, or whose every write commits beside a watched one.
UNWATCHED = {'api_schema': 'internal', 'api_changes': 'the log itself', 'api_document_basis': 'reuse key, never shown',
             'api_design_decisions': 'commits with the run update that moves past the gate',
             'api_task_features': 'written only with task.accepted'}


def bearer(who='operator'):
    return {'Authorization': 'Bearer ' + TOKENS[who]}


@pytest.fixture(autouse=True)
def loopback_only(_isolated, monkeypatch):
    """These tests need a real socket, to this machine and nowhere else."""
    def connect(sock, address):
        assert address[0] == '127.0.0.1', address
        return _connect(sock, address)

    def create_connection(address, *args, **kwargs):
        assert address[0] == '127.0.0.1', address
        return _create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket, 'create_connection', create_connection)


@pytest.fixture
def world(tmp_path):
    path = tmp_path / 'siamang.db'
    with Database(path) as db:
        registry = ProjectRegistry(db)
        project = registry.register(name='Banuva', root_path=str(sample_project(tmp_path / 'one')))
        other = registry.register(name='Lain', root_path=str(sample_project(tmp_path / 'two')))
    def principal(name, permissions, *projects):
        return Principal(name, hashlib.sha256(TOKENS[name].encode()).hexdigest(), frozenset(permissions), frozenset(projects))
    settings = APISettings(path, (principal('operator', {'read', 'submit', 'run'}, project.project_id),
                                  principal('other', {'read'}, other.project_id),
                                  principal('nothing', {'submit'}, project.project_id)), b'test-seal-material' * 3)
    return settings, project, other


class Live:
    """A real server on a real socket."""

    def __init__(self, settings, **kwargs):
        self.app = create_app(settings, **kwargs)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, host='127.0.0.1', port=self.port, log_level='warning'))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f'http://127.0.0.1:{self.port}'

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, 'server did not start'
            time.sleep(.01)
        return self

    def __exit__(self, *exc):
        self.hub.end_streams()
        self.server.should_exit = True
        self.thread.join(10)
        assert not self.thread.is_alive(), 'an open stream held the server up'

    @property
    def hub(self):
        return self.app.state.runtime.hub

    def post(self, path, body=None):
        return httpx.post(self.url + path, headers=bearer(), json=body or {}, timeout=10)

    def get(self, path, who='operator'):
        return httpx.get(self.url + path, headers=bearer(who), timeout=10)


class Frames:
    """Reads a stream on its own thread, so a test can wait for one frame with a deadline."""

    def __init__(self, url, who='operator', headers=None):
        self.frames, self.status, self.headers = queue.Queue(), None, {}
        self.ready = threading.Event()
        address = urlsplit(url)
        self.connection = http.client.HTTPConnection(address.hostname, address.port, timeout=30)
        path = address.path + (f'?{address.query}' if address.query else '')
        self.thread = threading.Thread(target=self._run, args=(path, {**bearer(who), **(headers or {})}), daemon=True)
        self.thread.start()
        assert self.ready.wait(10), 'no response'

    def _run(self, path, headers):
        try:
            self.connection.request('GET', path, headers=headers)
            response = self.connection.getresponse()
            self.status, self.headers = response.status, response.headers
            self.ready.set()
            current = {}
            for raw in response:
                line = raw.decode().rstrip('\n')
                if not line:
                    if 'event' in current:
                        self.frames.put(current)
                    current = {}
                elif ':' in line:
                    field, _, value = line.partition(':')
                    current[field] = value.strip()
        except (OSError, http.client.HTTPException, ValueError, AttributeError):
            pass  # closed by the test
        finally:
            self.ready.set()
            self.frames.put(None)

    def next(self, *events, within=5.0):
        """The next frame of one of ``events``, skipping others. Fails rather than waits for ever."""
        deadline = time.monotonic() + within
        while True:
            left = deadline - time.monotonic()
            assert left > 0, f'no {events} within {within}s'
            try:
                item = self.frames.get(timeout=left)
            except queue.Empty:
                raise AssertionError(f'no {events} within {within}s') from None
            assert item is not None, f'stream ended while waiting for {events}'
            if item['event'] in events:
                return item['event'], json.loads(item['data']), item.get('id')

    def silent(self, *events, within=.8):
        with pytest.raises(AssertionError, match='no '):
            self.next(*events, within=within)

    def close(self):
        if self.connection.sock:
            with contextlib.suppress(OSError):  # the server may have gone first
                self.connection.sock.shutdown(socket.SHUT_RDWR)
        self.thread.join(5)
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def task(db, project, request_id='req_stream'):
    return TaskEngine(db, ProjectRegistry(db)).create(TaskRequest.from_dict(
        {'project_id': project.project_id, 'description': INSTRUCTION, 'request_id': request_id, 'source': 'api'})).task


def initialized(path):
    db = Database(path)
    RunStore(db).initialize()
    return db


def kinds(db, after=0):
    return [r['kind'] for r in RunStore(db).changes(after, 1000)]


# --- the log


def test_every_table_is_watched_or_excluded_with_a_reason(world):
    with initialized(world[0].database) as db:
        tables = {r['name'] for r in db.conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'api_%'")}
    assert tables - {w[0] for w in WATCHED_WRITES} - set(UNWATCHED) == set()


def test_a_task_change_records_its_project_and_lifecycle(world):
    with initialized(world[0].database) as db:
        created = task(db, world[1])
        rows = RunStore(db).changes(0, 10)
    assert [(r['kind'], r['project_id'], r['task_id']) for r in rows] == [('lifecycle', world[1].project_id, created.task_id)]


def test_a_rolled_back_change_leaves_nothing(world):
    with initialized(world[0].database) as db:
        created, store = task(db, world[1]), RunStore(db)
        head = store.change_bounds()[0]
        with pytest.raises(ZeroDivisionError), db.transaction():
            store.add_message(world[1].project_id, created.task_id, 'owner', {'message': 'x'})
            1 / 0
        assert store.change_bounds()[0] == head


def test_a_heartbeat_adds_no_change_but_a_phase_does(world):
    with initialized(world[0].database) as db:
        created, store = task(db, world[1]), RunStore(db)
        run_id = store.create(created.task_id)
        store.update(run_id, state='running', phase='planning')
        head = store.change_bounds()[0]
        db.conn.execute('UPDATE api_runs SET heartbeat_at=? WHERE run_id=?', ('2030-01-01T00:00:00.000Z', run_id))
        assert store.change_bounds()[0] == head
        store.update(run_id, state='running', phase='analysis')
        assert kinds(db, head) == ['execution']


def test_each_watched_write_reaches_the_log(world):
    with initialized(world[0].database) as db:
        created, store = task(db, world[1]), RunStore(db)
        project_id, task_id = world[1].project_id, created.task_id
        run_id = store.create(task_id)
        expected = [
            (lambda: store.add_message(project_id, task_id, 'owner', {'message': 'x'}), 'messages', task_id),
            (lambda: store.add_message(project_id, None, 'owner', {'message': 'x'}), 'messages', None),
            (lambda: store.add_draft(project_id, 'x', {}), 'drafts', None),
            (lambda: store.create_feature(project_id, 't', 'd'), 'project', None),
            (lambda: store.set_spec(project_id, state='thinking'), 'project', None),
            (lambda: store.add_repo(project_id, 'a/b'), 'project', None),
            (lambda: store.update_repo(project_id, state='ready'), 'project', None),
            (lambda: store.add_branch(task_id, project_id, 'siamang/x'), 'task', task_id),
            (lambda: store.update_branch(task_id, publication='publishing'), 'task', task_id),
            (lambda: store.set_ci(task_id, 'abc', 'watching'), 'task', task_id),
            (lambda: store.set_ci(task_id, 'abc', 'passed'), 'task', task_id),
            (lambda: store.start_part(run_id, 'design', 1, 1, ('a',), 60), 'task', task_id),
            (lambda: db.conn.execute('UPDATE api_runs SET report=? WHERE run_id=?', ('{}', run_id)), 'reports', task_id),
            (lambda: store.add_decision(run_id, task_id, sign=lambda *a: 's', gate='design', level=0, risk='low',
                                        outcome='ask', actor='a'), 'autonomy', task_id),
        ]
        for write, kind, expected_task in expected:
            head = store.change_bounds()[0]
            write()
            rows = store.changes(head, 10)
            assert {(r['kind'], r['project_id'], r['task_id']) for r in rows} == {(kind, project_id, expected_task)}, kind


def test_upgrading_an_older_database_adds_the_log_and_keeps_its_rows(world):
    with initialized(world[0].database) as db:
        created = task(db, world[1])
        db.conn.execute('UPDATE api_schema SET version=6')
        db.conn.execute('DROP TRIGGER api_changes_bounded')
        for row in db.conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'api_change_%'").fetchall():
            db.conn.execute(f"DROP TRIGGER {row['name']}")
        db.conn.execute('DROP TABLE api_changes')
    with initialized(world[0].database) as db:
        assert db.conn.execute('SELECT version FROM api_schema').fetchone()[0] == 7
        store = RunStore(db)
        assert store.change_bounds() == (0, 0)
        run_id = store.create(created.task_id)
        assert kinds(db) == ['execution'] and run_id


def test_starting_twice_keeps_one_trigger_for_each_write(world):
    for _ in range(2):
        initialized(world[0].database).close()
    with initialized(world[0].database) as db:
        task(db, world[1])
        assert kinds(db) == ['lifecycle']


def test_the_log_bounds_itself(world):
    with initialized(world[0].database) as db:
        db.conn.execute("INSERT INTO sqlite_sequence(name,seq) SELECT 'api_changes', ? WHERE NOT EXISTS "
                        "(SELECT 1 FROM sqlite_sequence WHERE name='api_changes')", (CHANGE_RETENTION * 3 - 1,))
        for position in range(1, 2):
            db.conn.execute("INSERT INTO api_changes(position,kind) VALUES (?, 'x')", (position,))
        db.conn.execute("INSERT INTO api_changes(position,kind) VALUES (?, 'x')", (CHANGE_RETENTION * 3,))
        assert RunStore(db).change_bounds() == (CHANGE_RETENTION * 3, CHANGE_RETENTION * 3)


# --- frames


def test_a_frame_cannot_be_broken_by_a_line_break_in_a_value():
    text = frame('changed', {'x': 'a\n\ndata: forged\r\nevent: approval'}, 7)
    assert text.count('\n') == 4 and text.startswith('id: 7\nevent: changed\ndata: ') and text.endswith('\n\n')


# --- over a real connection


def test_stream_requires_a_token_and_the_read_permission(world):
    with Live(world[0]) as live:
        assert httpx.get(live.url + '/v0/stream', timeout=5).status_code == 401
        assert httpx.get(live.url + '/v0/stream', headers={'Authorization': 'Bearer wrong'}, timeout=5).status_code == 401
        assert live.get('/v0/stream', 'nothing').status_code == 403


def test_a_commit_from_any_connection_arrives_at_once(world):
    with Live(world[0]) as live, Frames(live.url + '/v0/stream') as stream:
        assert stream.status == 200 and stream.headers['content-type'].startswith('text/event-stream')
        assert stream.headers['cache-control'] == 'no-store'
        event, ready, last_id = stream.next('ready')
        assert ready['replay'] is False and str(ready['cursor']) == last_id
        begun = time.monotonic()
        with Database(world[0].database) as db:  # the CLI's path: another connection, no API code involved
            created = task(db, world[1])
        event, data, _ = stream.next('lifecycle', within=2)
        assert time.monotonic() - begun < 1.5
        assert data['project_id'] == world[1].project_id and data['task_id'] == created.task_id
        assert data['event']['type'] == 'task.accepted' and data['event']['seq'] == 1


def test_a_reader_never_sees_another_projects_changes(world):
    with Live(world[0]) as live, Frames(live.url + '/v0/stream', 'other') as stream:
        stream.next('ready')
        with Database(world[0].database) as db:
            RunStore(db).add_message(world[1].project_id, None, 'owner', {'message': 'rahasia proyek lain'})
            task(db, world[1])
            RunStore(db).add_message(world[2].project_id, None, 'owner', {'message': 'milik sendiri'})
        _, data, _ = stream.next('changed', 'lifecycle', 'execution')
        assert data['project_id'] == world[2].project_id  # the first thing it hears is its own


def test_a_change_without_a_known_project_is_withheld_even_from_an_unscoped_reader(world):
    settings = APISettings(world[0].database, (Principal('all', hashlib.sha256(TOKENS['operator'].encode()).hexdigest(),
                                                         frozenset({'read'}), frozenset({'*'})),), b'test-seal-material' * 3)
    with Live(settings) as live, Frames(live.url + '/v0/stream') as stream:
        stream.next('ready')
        with Database(world[0].database) as db:
            db.conn.execute("INSERT INTO api_changes(kind,project_id,task_id) VALUES ('task',NULL,'tsk_ghost')")
            RunStore(db).add_message(world[1].project_id, None, 'owner', {'message': 'x'})
        _, data, _ = stream.next('changed')
        assert data['project_id'] == world[1].project_id


def test_frames_carry_no_machine_paths_or_credentials(world):
    leak = '/Users/rafie/rahasia/kunci.pem'
    with Database(world[0].database) as db:
        RunStore(db).initialize()
        created = task(db, world[1])
        run_id = RunStore(db).create(created.task_id)
    with Live(world[0]) as live, Frames(live.url + '/v0/stream') as stream:
        stream.next('ready')
        with Database(world[0].database) as db:
            RunStore(db).update(run_id, state='running', phase='planning', retry_reason=f'gagal di {leak}')
        _, data, _ = stream.next('execution')
        assert leak not in json.dumps(data) and 'withheld' in data['event']['retry_reason']
        items = live.get(f'/v0/tasks/{created.task_id}/execution-events').json()['items']
        assert leak not in json.dumps(items)  # the HTTP route gives the same value


def test_resuming_misses_nothing_and_repeats_nothing(world):
    with Database(world[0].database) as db:
        RunStore(db).initialize()
        created = task(db, world[1])
        run_id = RunStore(db).create(created.task_id)
    with Live(world[0]) as live:
        with Frames(live.url + '/v0/stream') as stream:
            _, ready, _ = stream.next('ready')
            with Database(world[0].database) as db:
                RunStore(db).update(run_id, state='running', phase='planning')
            _, heard, last_id = stream.next('execution')
            assert heard['event']['phase'] == 'planning'
        with Database(world[0].database) as db:  # nobody is connected for these
            RunStore(db).update(run_id, state='running', phase='analysis')
            RunStore(db).update(run_id, state='running', phase='programming')
        with Frames(live.url + '/v0/stream', headers={'Last-Event-ID': last_id}) as stream:
            assert stream.next('ready')[1]['replay'] is True
            assert stream.next('execution')[1]['event']['phase'] == 'analysis'
            assert stream.next('execution')[1]['event']['phase'] == 'programming'
            stream.silent('execution')


def test_the_header_wins_over_the_query_cursor_and_a_bad_header_is_ignored(world):
    with initialized(world[0].database) as db:
        task(db, world[1], 'req_a')
        task(db, world[1], 'req_b')
    with Live(world[0]) as live:
        with Frames(live.url + '/v0/stream?after=0', headers={'Last-Event-ID': '1'}) as stream:
            stream.next('ready')
            assert stream.next('lifecycle')[2] == '2'
        with Frames(live.url + '/v0/stream?after=0', headers={'Last-Event-ID': 'abc'}) as stream:
            assert stream.next('ready')[1]['replay'] is True
            assert stream.next('lifecycle')[2] == '1'
        assert live.get('/v0/stream?after=-1').status_code == 422


@pytest.mark.parametrize('cursor', [999, 0])
def test_a_cursor_outside_the_log_asks_for_a_reload_instead_of_going_silent(world, cursor):
    with initialized(world[0].database) as db:
        task(db, world[1])
        if cursor == 0:  # the rows this cursor needs were pruned
            db.conn.execute('DELETE FROM api_changes')
            db.conn.execute("INSERT INTO api_changes(position,kind) VALUES (500,'x')")
    with Live(world[0]) as live, Frames(f'{live.url}/v0/stream?after={cursor}') as stream:
        _, ready, _ = stream.next('ready')
        assert ready['replay'] is False
        with Database(world[0].database) as db:
            RunStore(db).add_message(world[1].project_id, None, 'owner', {'message': 'x'})
        assert stream.next('changed')[1]['parts'] == ['messages']  # and it follows from the head


def test_a_burst_to_one_task_becomes_one_frame_with_every_part(world):
    with initialized(world[0].database) as db:
        created, store = task(db, world[1]), RunStore(db)
        head = store.change_bounds()[0]
        run_id = store.create(created.task_id)
        tail = store.change_bounds()[0]
        with db.transaction():
            store.add_message(world[1].project_id, created.task_id, 'owner', {'message': 'x'})
            store.start_part(run_id, 'design', 1, 1, ('a',), 60)
            store.start_part(run_id, 'design', 2, 1, ('b',), 60)
    with Live(world[0]) as live:  # opening marks the unfinished parts interrupted: those commits are in the log as well
        with Database(world[0].database) as db:
            last = [r for r in RunStore(db).changes(tail, 100) if r['kind'] == 'task'][-1]['position']
        stream = Frames(f'{live.url}/v0/stream?after={tail}')
        _, data, frame_id = stream.next('changed')
        stream.close()
        assert data['parts'] == ['messages', 'task'] and data['task_id'] == created.task_id
        assert frame_id == str(last) and head < tail  # resuming after this id has already heard about all of them


def test_a_silent_stream_still_pings_so_a_client_can_tell_it_is_alive(world):
    import asyncio

    async def run():
        hub = ChangeHub(world[0].database)
        reader = world[0].principals[0]
        with initialized(world[0].database):
            pass
        stream = follow(hub, reader, ping_secs=.05)
        heard = [await stream.__anext__(), await stream.__anext__()]
        await stream.aclose()
        assert hub.count == 0
        hub.stop()
        return heard

    ready, ping = asyncio.run(run())
    assert 'event: ready' in ready and 'event: ping' in ping and ping.startswith('id: ')


def test_a_row_that_cannot_be_read_becomes_a_reload_and_the_stream_goes_on(world):
    with initialized(world[0].database) as db:
        created = task(db, world[1])
        head = RunStore(db).change_bounds()[0]
        db.conn.execute("INSERT INTO api_changes(kind,project_id,task_id,ref) VALUES ('lifecycle',?,?,'evt_gone')",
                        (world[1].project_id, created.task_id))
        RunStore(db).add_message(world[1].project_id, None, 'owner', {'message': 'sesudahnya'})
    with Live(world[0]) as live, Frames(f'{live.url}/v0/stream?after={head}') as stream:
        _, data, _ = stream.next('changed')
        assert data == {'project_id': world[1].project_id, 'task_id': created.task_id, 'parts': ['task']}
        _, data, _ = stream.next('changed')
        assert data['parts'] == ['messages'] and data['task_id'] is None


def test_a_reader_never_sees_another_projects_heartbeats(world):
    with initialized(world[0].database) as db:
        created, store = task(db, world[1]), RunStore(db)
        run_id = store.create(created.task_id)
        store.update(run_id, state='running', phase='planning')
    with Live(world[0]) as live, Frames(live.url + '/v0/stream', 'other') as outsider, Frames(live.url + '/v0/stream') as owner:
        outsider.next('ready')
        owner.next('ready')
        with Database(world[0].database) as db:  # opening the runtime marked it interrupted: running again, as a worker
            db.conn.execute("UPDATE api_runs SET state='running', heartbeat_at='2030-01-01T00:00:00.000Z' WHERE run_id=?", (run_id,))
        assert owner.next('heartbeat')[1]['task_id'] == created.task_id
        outsider.silent('heartbeat')


def test_a_closed_connection_frees_its_place(world):
    with Live(world[0]) as live:
        with Frames(live.url + '/v0/stream') as stream:
            stream.next('ready')
            assert live.hub.count == 1
        deadline = time.monotonic() + 5
        while live.hub.count and time.monotonic() < deadline:
            time.sleep(.02)
        assert live.hub.count == 0


def test_too_many_streams_are_refused_and_later_ones_are_let_in(world):
    with Live(world[0]) as live:
        live.hub.max_streams = 1
        with Frames(live.url + '/v0/stream') as first:
            first.next('ready')
            response = live.get('/v0/stream')
            assert response.status_code == 503 and response.json()['error']['code'] == 'stream_unavailable'
        deadline = time.monotonic() + 5
        while live.hub.count and time.monotonic() < deadline:
            time.sleep(.02)
        with Frames(live.url + '/v0/stream') as again:
            assert again.next('ready')[0] == 'ready'


def test_a_real_run_streams_its_stages_and_heartbeats(world):
    # A heartbeat is the only thing that keeps a client's activity display honest once it stops polling.
    release, answered = threading.Event(), threading.Event()

    def slow(model, request):
        answered.set()
        assert release.wait(30)
        return CLEAN

    model = make_model(slow)
    with Live(world[0], models=model.router) as live, Frames(live.url + '/v0/stream') as stream:
        stream.next('ready')
        response = live.post('/v0/tasks', {'project_id': world[1].project_id, 'description': INSTRUCTION, 'request_id': 'req_real'})
        task_id = response.json()['task']['task_id']
        assert stream.next('lifecycle')[1]['event']['type'] == 'task.accepted'
        assert live.post(f'/v0/tasks/{task_id}/run').status_code == 202
        assert stream.next('execution')[1]['event']['state'] == 'scheduled'
        assert answered.wait(15)
        beats = [stream.next('heartbeat', within=8)[1] for _ in range(3)]
        assert {b['task_id'] for b in beats} == {task_id} and beats[0]['project_id'] == world[1].project_id
        assert beats[0]['heartbeat_at'] < beats[1]['heartbeat_at'] < beats[2]['heartbeat_at']  # only when it changes
        release.set()
        seen = []
        while not seen or seen[-1] not in ('awaiting_approval', 'blocked', 'failed'):
            seen.append(stream.next('execution', within=20)[1]['event']['phase'])
        rows = live.get(f'/v0/tasks/{task_id}/execution-events').json()['items']
        assert [r['phase'] for r in rows][-len(seen):] == seen  # the same history the HTTP route replays


def test_a_stream_ends_when_the_server_is_told_to_stop(tmp_path, world):
    """Without this, one open browser tab keeps ``siamang-api`` alive for ever after SIGTERM."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = {**os.environ, 'SIAMANG_STORAGE_PATH': str(world[0].database), 'SIAMANG_API_SEAL_KEY': 'ab' * 32,
           'SIAMANG_API_IDENTITIES': json.dumps({'op': {'token_sha256': hashlib.sha256(TOKENS['operator'].encode()).hexdigest(),
                                                        'permissions': ['read'], 'projects': ['*']}}),
           'SIAMANG_API_ORIGINS': '[]', 'PYTHONPATH': str(Path(__file__).parents[2] / 'src')}
    env.pop('SIAMANG_CONFIG', None)
    server = subprocess.Popen([sys.executable, '-m', 'siamang.api.main', '--port', str(port)], env=env, cwd=tmp_path,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                if httpx.get(f'http://127.0.0.1:{port}/v0/health', headers=bearer(), timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline and server.poll() is None, 'server did not start'
            time.sleep(.05)
        with Frames(f'http://127.0.0.1:{port}/v0/stream') as stream:
            stream.next('ready')
            begun = time.monotonic()
            server.send_signal(signal.SIGTERM)
            output, _ = server.communicate(timeout=4)  # raises rather than waits on a hang
            assert time.monotonic() - begun < 3
            assert 'Cancel' not in output, output  # it ended by itself; the backstop was not needed
    finally:
        if server.poll() is None:
            server.kill()
            server.communicate()
