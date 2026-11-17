"""Every model call is attributed, timed, and counted, including the failed ones; measuring never breaks the work."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from siamang.models import GenerationError, ModelRequest, ModelTimeoutError, ProviderOutput, ResponseFormat, Usage
from siamang.models import metering
from tests.support.fake_models import FakeProvider, VendorTimeout, fake_model

CAPS = ('text_generation', 'structured_output')


def provider(**kw):
    model = fake_model(caps=CAPS)
    return FakeProvider(models=[model], **kw), model


def recorder():
    records, lock = [], threading.Lock()

    def sink(kind, record):
        with lock:
            records.append((kind, dict(record)))
    return records, sink


def calls(records):
    return [r for kind, r in records if kind == 'call']


def test_nothing_is_recorded_outside_a_scope():
    p, model = provider()
    assert p.generate(model, ModelRequest.from_prompt('hi')).usage == Usage(3, 2)
    assert metering.current().sink is None


def test_a_call_carries_its_scope_tokens_and_time():
    p, model = provider()
    records, sink = recorder()
    request = ModelRequest.from_prompt('hi', metadata={'purpose': 'documents', 'stage': 'planning'})
    with metering.scope(sink=sink, project_id='prj_a', task_id='tsk_a', run_id='request_a'):
        p.generate(model, request)
    [call] = calls(records)
    assert call | {'started_at': None, 'finished_at': None, 'duration_ms': None} == {
        'project_id': 'prj_a', 'task_id': 'tsk_a', 'run_id': 'request_a', 'draft_id': None, 'agent': None,
        'stage': 'planning', 'package': None, 'request_id': request.request_id, 'purpose': 'documents',
        'provider_id': 'fake', 'model_id': 'fake-a', 'outcome': 'ok', 'input_tokens': 3, 'output_tokens': 2,
        'cache_read_tokens': None, 'cache_write_tokens': None,
        'started_at': None, 'finished_at': None, 'duration_ms': None}
    assert call['started_at'] <= call['finished_at'] and call['started_at'].endswith('Z')
    assert isinstance(call['duration_ms'], int) and call['duration_ms'] >= 0


def test_a_timeout_is_recorded_with_unknown_tokens():
    p, model = provider(fail_with=VendorTimeout('slow'))
    records, sink = recorder()
    with metering.scope(sink=sink, project_id='prj_a'), pytest.raises(ModelTimeoutError):
        p.generate(model, ModelRequest.from_prompt('hi'))
    [call] = calls(records)
    assert call['outcome'] == 'timeout' and call['input_tokens'] is None and call['output_tokens'] is None


def test_an_unusable_reply_still_counts_its_tokens():
    p, model = provider(reply=lambda m, r: ProviderOutput('not json', usage=Usage(40, 7, cache_read_tokens=30)))
    records, sink = recorder()
    with metering.scope(sink=sink, project_id='prj_a'), pytest.raises(GenerationError) as caught:
        p.generate(model, ModelRequest.from_prompt('hi', response_format=ResponseFormat.JSON))
    assert caught.value.usage == Usage(40, 7, cache_read_tokens=30)
    [call] = calls(records)
    assert (call['outcome'], call['input_tokens'], call['output_tokens'], call['cache_read_tokens']) == (
        'generation_error', 40, 7, 30)


def test_an_activity_names_the_agent_and_is_timed_however_it_ends():
    p, model = provider()
    records, sink = recorder()
    with metering.scope(sink=sink, project_id='prj_a', task_id='tsk_a'):
        with metering.activity('analyst', 'analysis'):
            p.generate(model, ModelRequest.from_prompt('hi', metadata={'stage': 'designing'}))
        with pytest.raises(RuntimeError), metering.activity('programmer', 'programming', package='api'):
            raise RuntimeError('boom')
    [call] = calls(records)
    assert (call['agent'], call['stage']) == ('analyst', 'analysis')  # the activity, not the request's own label
    spans = [r for kind, r in records if kind == 'activity']
    assert [(s['agent'], s['stage'], s['package'], s['outcome']) for s in spans] == [
        ('analyst', 'analysis', None, 'done'), ('programmer', 'programming', 'api', 'failed')]
    assert all(s['task_id'] == 'tsk_a' and s['duration_ms'] >= 0 for s in spans)
    assert metering.current().agent is None  # the scope ends with its block


def test_a_failing_sink_never_breaks_the_work(caplog):
    def sink(kind, record):
        raise OSError('disk full')
    p, model = provider()
    with caplog.at_level(logging.WARNING, logger='siamang'), metering.scope(sink=sink, project_id='prj_a'):
        with metering.activity('lead', 'briefing'):
            assert p.generate(model, ModelRequest.from_prompt('hi')).content
    assert 'dropped a call usage record: OSError' in caplog.text
    assert 'dropped a activity usage record: OSError' in caplog.text


def test_submit_carries_the_scope_into_a_pool_and_a_plain_submit_does_not():
    p, model = provider()
    records, sink = recorder()
    with metering.scope(sink=sink, project_id='prj_a', task_id='tsk_a'), metering.activity('designer', 'designing'):
        with ThreadPoolExecutor(max_workers=3) as pool:
            carried = [metering.submit(pool, p.generate, model, ModelRequest.from_prompt(f'part {i}')) for i in range(3)]
            lost = pool.submit(p.generate, model, ModelRequest.from_prompt('lost'))
            for future in [*carried, lost]:
                future.result()
    seen = calls(records)
    assert len(seen) == 3 and {(c['task_id'], c['agent'], c['stage']) for c in seen} == {('tsk_a', 'designer', 'designing')}


def test_scope_refuses_fields_it_does_not_know():
    with pytest.raises(TypeError), metering.scope(tokens=5):
        pass
