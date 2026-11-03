"""QARequest: exact snapshots, recorded patch hash, no execution/repair/approval keys, deep-frozen metadata."""

import dataclasses

import pytest

from siamang.analyst import SystemAnalysis
from siamang.planner import Plan
from siamang.programmer import Patch
from siamang.qa import QARequest
from siamang.qa.errors import QAErrorCode, QARequestError
from siamang.qa.request import QA_KEYS
from tests.support.programmer import make_setup
from tests.support.qa import propose, qa_request


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


@pytest.fixture
def patch(setup):
    return propose(setup)


def test_valid_request(setup, patch):
    r = qa_request(setup, patch, metadata={"ticket": {"id": "QA-1", "tags": ["a"]}})
    d = r.to_dict()
    assert d["patch_id"] == patch.patch_id and d["patch_hash"] == patch.content_hash
    assert d["plan_id"] == setup.plan.plan_id and d["analysis_id"] == setup.analysis.analysis_id


def test_request_is_deeply_immutable(setup, patch):
    r = qa_request(setup, patch, metadata={"ticket": {"tags": ["a"]}})
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.patch_hash = "x"
    for mutate in (lambda: r.metadata.__setitem__("x", 1), lambda: r.metadata["ticket"].__setitem__("x", 1),
                   lambda: r.metadata["ticket"]["tags"].append("b")):
        with pytest.raises((TypeError, AttributeError)):
            mutate()


@pytest.mark.parametrize("key", ["fix", "repair", "retry", "review", "approve", "reject", "run_tests", "pytest",
                                 "command", "shell", "apply", "write_file", "programmer", "loop", "agent_id",
                                 "tool", "model_id", "path", "private_key", "commit"])
def test_forbidden_metadata_keys_at_any_depth(setup, patch, key):
    for value in ({key: 1}, {"a": {key: 1}}, {"a": [{"b": [{key.upper(): 1}]}]}):
        with pytest.raises(QARequestError) as info:
            qa_request(setup, patch, metadata=value)
        assert info.value.code is QAErrorCode.REQUEST_INVALID
    assert {"fix", "repair", "retry", "review", "approve"} <= QA_KEYS


def test_snapshots_must_be_exact(setup, patch):
    class LivePatch(Patch):
        pass

    live = LivePatch(**{f.name: getattr(patch, f.name) for f in dataclasses.fields(Patch)})
    live_plan = type("LivePlan", (Plan,), {})(**{f.name: getattr(setup.plan, f.name) for f in dataclasses.fields(Plan)})
    live_analysis = type("LiveAnalysis", (SystemAnalysis,), {})(
        **{f.name: getattr(setup.analysis, f.name) for f in dataclasses.fields(SystemAnalysis)})
    for over in ({"patch": live}, {"patch": patch.to_dict()}, {"patch": None}, {"plan": live_plan},
                 {"plan": setup.plan.to_dict()}, {"analysis": live_analysis}, {"analysis": object()}):
        over = dict(over)
        with pytest.raises(QARequestError):
            qa_request(setup, over.pop("patch", patch), "sha256:" + "0" * 64, **over)


@pytest.mark.parametrize("value", ["", "sha256:abc", "md5:" + "0" * 64, "sha256:" + "G" * 64, "SHA256:" + "0" * 64,
                                   None, 42, "sha256:" + "0" * 64 + "\n"])
def test_patch_hash_format(setup, patch, value):
    with pytest.raises(QARequestError):
        QARequest(setup.world.task.task_id, setup.world.project.project_id, setup.plan, setup.analysis, patch, value)


@pytest.mark.parametrize("over", [{"task_id": "tsk_../x"}, {"task_id": ""}, {"project_id": "prj x"},
                                  {"project_id": None}, {"metadata": "x"}, {"metadata": {"k": object()}},
                                  {"metadata": {"k": "x" * 20_000}}])
def test_invalid_fields(setup, patch, over):
    with pytest.raises(QARequestError):
        qa_request(setup, patch, **over)
