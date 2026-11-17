"""What models cost a project, a task, and each agent, in tokens and in time. Read-only: computed from the record.

Tokens are summed as the providers reported them. A call reported without counts (a timeout) is counted as a call,
never guessed at, and named in ``unreported_calls``. ``input_tokens`` includes the prompt cache, which
``cache_read_tokens`` and ``cache_write_tokens`` break out.

Time comes in kinds that are never mixed:

* ``active_ms`` of an agent: the union of its activities in a task, so Programmers working side by side count once.
  Measured around the work itself, model or not: QA and the Reviewer check without a model.
* ``model_ms``: the calls' own durations, added up.
* ``work_ms`` and ``waiting_ms`` of a task: how long its runs were running, and how long they waited for the owner, from
  the execution events every run records anyway. A task from before token recording still has these.

A draft's conversation with the Lead belongs to the task it became. The project's intake and drafts that never became a
task are ``outside_tasks``. A project's totals are its tasks plus that, so the numbers add up.
"""
from datetime import datetime

from siamang.documents.catalog import AGENTS

UNKNOWN = 'unknown'  # a call outside any activity: none is expected, and it is shown rather than lost
OPEN_STATES = ('scheduled', 'running', 'waiting')
# The task a record belongs to: its own, or for a draft's conversation the task the draft became.
_OWNER = 'COALESCE({t}.task_id, d.task_id)'
_DRAFTS = 'LEFT JOIN api_task_drafts d ON {t}.task_id IS NULL AND d.draft_id = {t}.draft_id'


def _owned_by(t, count):
    """The records of ``count`` tasks, their drafts' conversations included. Both sides use an index, unlike a filter
    on the owner itself: a task list is read on every stage change. Its parameters are the task ids, twice."""
    marks = ','.join('?' * count)
    return (f'({t}.task_id IN ({marks}) OR ({t}.task_id IS NULL AND {t}.draft_id IN '
            f'(SELECT draft_id FROM api_task_drafts WHERE task_id IN ({marks}))))')
_TOKENS = ("COUNT(*) AS calls, COALESCE(SUM(c.outcome != 'ok'), 0) AS failed_calls, "
           'COALESCE(SUM(c.input_tokens IS NULL OR c.output_tokens IS NULL), 0) AS unreported_calls, '
           'COALESCE(SUM(c.input_tokens), 0) AS input_tokens, COALESCE(SUM(c.output_tokens), 0) AS output_tokens, '
           'COALESCE(SUM(c.cache_read_tokens), 0) AS cache_read_tokens, '
           'COALESCE(SUM(c.cache_write_tokens), 0) AS cache_write_tokens, COALESCE(SUM(c.duration_ms), 0) AS model_ms')
_COUNTS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'calls', 'failed_calls',
           'unreported_calls', 'model_ms')


def to_ms(stamp):
    return datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000


def union_ms(spans):
    """How long at least one of the ``(start, end)`` spans was open."""
    total, reach = 0.0, float('-inf')
    for start, end in sorted(spans):
        if end > reach:
            total += end - max(start, reach)
            reach = end
    return round(total)


def tokens(row=None):
    out = {k: (row[k] if row is not None else 0) or 0 for k in _COUNTS}
    out['total_tokens'] = out['input_tokens'] + out['output_tokens']
    return out


def _add(a, b):
    out = {k: a[k] + b[k] for k in _COUNTS}
    out['total_tokens'] = out['input_tokens'] + out['output_tokens']
    return out


def _order(agent):
    return AGENTS.index(agent) if agent in AGENTS else len(AGENTS)


def run_time(db, task_ids, now_ms):
    """``{task_id: (work_ms, waiting_ms)}`` from each run's state changes.

    A run still open counts up to now. A run cut off by a restart ends at its last heartbeat, which startup writes into
    the ``interrupted`` event, so the hours the server was down are not counted as work.
    """
    out = {t: [0.0, 0.0] for t in task_ids}
    if not task_ids:
        return {}
    marks = ','.join('?' * len(task_ids))
    rows = db.conn.execute(
        "SELECT task_id, run_id, timestamp, json_extract(payload, '$.state') AS state, "
        "json_extract(payload, '$.last_heartbeat') AS last_heartbeat "
        f'FROM api_run_events WHERE task_id IN ({marks}) ORDER BY position', tuple(task_ids)).fetchall()
    runs = {}
    for r in rows:
        runs.setdefault((r['task_id'], r['run_id']), []).append(r)
    for (task_id, _), events in runs.items():
        for i, event in enumerate(events):
            start, after = to_ms(event['timestamp']), events[i + 1] if i + 1 < len(events) else None
            if after is None:
                end = now_ms if event['state'] in OPEN_STATES else start
            else:
                end = to_ms(after['timestamp'])
                if event['state'] == 'running' and after['state'] == 'interrupted' and after['last_heartbeat']:
                    end = min(end, max(start, to_ms(after['last_heartbeat'])))
            if event['state'] == 'running':
                out[task_id][0] += max(0.0, end - start)
            elif event['state'] == 'waiting':
                out[task_id][1] += max(0.0, end - start)
    return {t: (round(w), round(s)) for t, (w, s) in out.items()}


def _agents(token_rows, spans):
    """Rows per agent: tokens from the calls, ``active_ms`` from the activities, per task then added up."""
    by_agent = {}
    for r in token_rows:
        agent = r['agent'] or UNKNOWN
        row = by_agent.setdefault(agent, dict(agent=agent, models=[], **tokens(), active_ms=0))
        row.update(_add(row, tokens(r)))
        row['models'] = sorted(set(row['models']) | set(filter(None, (r['models'] or '').split(','))))
    grouped = {}
    for owner, agent, start, end in spans:
        grouped.setdefault((owner, agent), []).append((start, end))
    for (_, agent), intervals in grouped.items():
        row = by_agent.setdefault(agent, dict(agent=agent, models=[], **tokens(), active_ms=0))
        row['active_ms'] += union_ms(intervals)
    return sorted(by_agent.values(), key=lambda r: (_order(r['agent']), r['agent']))


def _spans(db, where, params):
    rows = db.conn.execute(
        f"SELECT {_OWNER.format(t='a')} AS owner, a.agent, a.started_at, a.finished_at FROM api_agent_activity a "
        f"{_DRAFTS.format(t='a')} WHERE {where}", params).fetchall()
    return [(r['owner'], r['agent'], to_ms(r['started_at']), to_ms(r['finished_at'])) for r in rows]


def task_usage(db, task, since, observed_at):
    ids = (task.task_id, task.task_id)
    rows = db.conn.execute(
        f"SELECT c.agent AS agent, GROUP_CONCAT(DISTINCT c.model_id) AS models, {_TOKENS} FROM api_model_calls c "
        f"WHERE {_owned_by('c', 1)} GROUP BY c.agent", ids).fetchall()
    agents = _agents(rows, _spans(db, _owned_by('a', 1), ids))
    total = tokens()
    for row in agents:
        total = _add(total, row)
    work, waiting = run_time(db, [task.task_id], to_ms(observed_at))[task.task_id]
    return dict(task_id=task.task_id, project_id=task.project_id, since=since, complete=_complete(task, since),
                totals=dict(total, work_ms=work, waiting_ms=waiting), agents=agents, observed_at=observed_at)


def project_usage(db, project, since, observed_at):
    owner = _OWNER.format(t='c')
    by_owner = {r['owner']: tokens(r) for r in db.conn.execute(
        f"SELECT {owner} AS owner, {_TOKENS} FROM api_model_calls c {_DRAFTS.format(t='c')} "
        'WHERE c.project_id = ? GROUP BY owner', (project.project_id,))}
    rows = db.conn.execute(
        f"SELECT c.agent AS agent, GROUP_CONCAT(DISTINCT c.model_id) AS models, {_TOKENS} FROM api_model_calls c "
        'WHERE c.project_id = ? GROUP BY c.agent', (project.project_id,)).fetchall()
    spans = _spans(db, 'a.project_id = ?', (project.project_id,))
    tasks = db.conn.execute('SELECT task_id, title, status, created_at FROM tasks WHERE project_id = ?',
                            (project.project_id,)).fetchall()
    times = run_time(db, [t['task_id'] for t in tasks], to_ms(observed_at))
    newest_first = sorted(tasks, key=lambda t: t['created_at'], reverse=True)
    items = [dict(task_id=t['task_id'], title=t['title'], status=t['status'], **by_owner.get(t['task_id'], tokens()),
                  work_ms=times[t['task_id']][0], waiting_ms=times[t['task_id']][1]) for t in newest_first]
    items.sort(key=lambda t: -t['total_tokens'])  # stable: equal totals stay newest first
    outside_spans = {}
    for owner_id, agent, start, end in spans:
        if owner_id is None:
            outside_spans.setdefault(agent, []).append((start, end))
    outside = dict(by_owner.get(None, tokens()), active_ms=sum(union_ms(s) for s in outside_spans.values()))
    total = tokens()
    for value in by_owner.values():
        total = _add(total, value)
    return dict(project_id=project.project_id, since=since, complete=_complete(project, since),
                totals=dict(total, work_ms=sum(t['work_ms'] for t in items), waiting_ms=sum(t['waiting_ms'] for t in items)),
                agents=_agents(rows, spans), tasks=items, outside_tasks=outside, observed_at=observed_at)


def task_summaries(db, task_ids, observed_at):
    """``{task_id: usage}`` for a list: tokens and work time only."""
    if not task_ids:
        return {}
    by_owner = {r['owner']: tokens(r) for r in db.conn.execute(
        f"SELECT {_OWNER.format(t='c')} AS owner, {_TOKENS} FROM api_model_calls c {_DRAFTS.format(t='c')} "
        f"WHERE {_owned_by('c', len(task_ids))} GROUP BY owner", (*task_ids, *task_ids))}
    times = run_time(db, list(task_ids), to_ms(observed_at))
    return {t: summary(by_owner.get(t), work_ms=times[t][0]) for t in task_ids}


def project_summaries(db):
    """``{project_id: usage}`` for every project with a record: tokens only."""
    return {r['project_id']: summary(tokens(r)) for r in db.conn.execute(
        f'SELECT c.project_id AS project_id, {_TOKENS} FROM api_model_calls c GROUP BY c.project_id')}


def summary(value=None, **extra):
    """What a list shows. Nothing recorded yet reads as zero calls, not as missing."""
    value = value or tokens()
    keys = ('input_tokens', 'output_tokens', 'total_tokens', 'calls', 'unreported_calls')
    return {k: value[k] for k in keys} | extra


def _complete(item, since):
    """Whether recording began before the item did. Earlier work has no token record."""
    return bool(since) and item.created_at.timestamp() * 1000 >= to_ms(since)
