"""Server-Sent Events: what committed, in commit order. The HTTP routes stay the source of every other value.

``api_changes`` is the log (see ``WATCHED_WRITES``), so each stream only keeps a cursor into it. A slow reader falls behind
in the database, not in memory, and resuming is the same read as following. One thread watches ``PRAGMA data_version``,
which changes only when another connection commits, and wakes the streams; it never decides what they send.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable

from siamang.log import get_logger
from siamang.storage import Database, EventStore

from .store import RunStore, now

log = get_logger('api.stream')

POLL_SECS = .2
PING_SECS = 10  # a client that hears nothing for much longer than this may assume the connection is dead
MAX_STREAMS = 8
BATCH = 200
RETRY_MILLIS = 3000


class ChangeHub:
    """Tells open streams that something committed. Holds no events, so it can neither lose nor reorder them."""

    def __init__(self, path, *, poll=POLL_SECS, max_streams=MAX_STREAMS):
        self.path, self.poll, self.max_streams = path, poll, max_streams
        self.ending = False
        self.heartbeats: tuple[dict, ...] = ()
        self._streams: dict[asyncio.Event, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    @property
    def count(self):
        return len(self._streams)

    @property
    def full(self):
        return self.ending or self.count >= self.max_streams

    def attach(self):
        wake = asyncio.Event()
        with self._lock:
            self._streams[wake] = asyncio.get_running_loop()
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._watch, name='siamang-changes', daemon=True)
                self._thread.start()
        return wake

    def detach(self, wake):
        with self._lock:
            self._streams.pop(wake, None)

    def end_streams(self):
        """Safe in a signal handler: it takes no lock. The watcher sees the flag and wakes every stream."""
        self.ending = True

    def stop(self):
        self.ending = True
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join()
            self._thread = None
        self._wake()

    def _wake(self):
        with self._lock:
            streams = list(self._streams.items())
        for wake, loop in streams:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # that stream's loop has closed; nothing is left to wake
                self.detach(wake)

    def _watch(self):
        db, seen = None, None
        while not self._stop.wait(self.poll):
            if self.ending:
                self._wake()
                continue
            if not self._streams:  # nobody is listening: no reason to touch the database
                seen = None
                continue
            try:
                db = db or Database(self.path)
                version = db.conn.execute('PRAGMA data_version').fetchone()[0]
                if version != seen:
                    seen = version
                    self.heartbeats = tuple(RunStore(db).running_heartbeats())
                    self._wake()
            except sqlite3.Error as exc:
                log.warning('change watcher will retry: %s', type(exc).__name__)
                if db:
                    db.close()
                db, seen = None, None
        if db:
            db.close()


def frame(event, data, position=None):
    # json.dumps escapes every line break, so a value can never end the frame or start another field.
    head = f'id: {position}\n' if position is not None else ''
    return f'{head}event: {event}\ndata: {json.dumps(data, separators=(",", ":"))}\n\n'


def _bounds(path):
    with Database(path) as db:
        return RunStore(db).change_bounds()


def _read(path, after, principal, clean):
    """One batch this principal may see, oldest first, as ``(frames, last position, rows read, whether rows were missed)``.

    Adjacent changes to the same task become one frame, under the last position, so a reader that resumes after that id
    has already heard about all of them. Anything without a known project is withheld, whoever is asking.
    """
    frames, group = [], None

    def flush():
        nonlocal group
        if group:
            frames.append(frame('changed', dict(project_id=group['project_id'], task_id=group['task_id'],
                                                parts=sorted(group['parts'])), group['position']))
            group = None

    with Database(path) as db:
        store, events = RunStore(db), EventStore(db)
        rows = store.changes(after, BATCH)
        missed = after + 1 < store.change_bounds()[1]  # fell behind what is kept while connected
        for row in rows:
            project_id, task_id = row['project_id'], row['task_id']
            if not project_id or not principal.allows_project(project_id):
                continue
            kind = row['kind']
            if kind in ('lifecycle', 'execution'):
                try:
                    event = events.get(row['ref']).to_dict() if kind == 'lifecycle' else store.execution_event(int(row['ref']))
                    item = frame(kind, clean(dict(project_id=project_id, task_id=task_id, event=event)), row['position'])
                except Exception as exc:  # a row that cannot be read must not stop the stream at this position for ever
                    log.warning('sending a reload for change %s: %s', row['position'], type(exc).__name__)
                    kind = 'task'
                else:
                    flush()
                    frames.append(item)
                    continue
            if group and (group['project_id'], group['task_id']) != (project_id, task_id):
                flush()
            group = group or dict(project_id=project_id, task_id=task_id, parts=set())
            group['parts'].add(kind)
            group['position'] = row['position']
        flush()
    return frames, rows[-1]['position'] if rows else after, len(rows), missed


async def follow(hub: ChangeHub, principal, cursor: int | None = None,
                 clean: Callable = lambda value: value, ping_secs: float = PING_SECS) -> AsyncIterator[str]:
    """Follow the log from ``cursor``. ``ready`` says whether a reader's state is whole (``replay``) or must be reloaded."""
    wake = hub.attach()
    beats: dict[str, str] = {}
    try:
        head, oldest = await asyncio.to_thread(_bounds, hub.path)
        # A cursor ahead of the log is from another database; one behind what is kept has missed changes for good.
        replay = cursor is not None and cursor <= head and (not oldest or cursor + 1 >= oldest)
        cursor = cursor if replay else head
        yield f'retry: {RETRY_MILLIS}\n\n' + frame('ready', dict(cursor=cursor, replay=replay), cursor)
        while not hub.ending:
            wake.clear()  # before reading: a commit during the read wakes the next wait
            frames, cursor, rows, missed = await asyncio.to_thread(_read, hub.path, cursor, principal, clean)
            if missed:
                yield frame('ready', dict(cursor=cursor, replay=False), cursor)
            for item in frames:
                yield item
            current = [b for b in hub.heartbeats if b['project_id'] and principal.allows_project(b['project_id'])]
            for beat in current:
                if beats.get(beat['run_id']) != beat['heartbeat_at']:
                    yield frame('heartbeat', beat)
            beats = {b['run_id']: b['heartbeat_at'] for b in current}
            if rows == BATCH:
                continue
            try:
                async with asyncio.timeout(ping_secs):
                    await wake.wait()
            except TimeoutError:
                yield frame('ping', dict(at=now()), cursor)
    except sqlite3.Error as exc:  # the client reconnects with its last id and misses nothing
        log.warning('event stream closed: %s', type(exc).__name__)
    finally:
        hub.detach(wake)
