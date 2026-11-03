"""ReviewRequest: exact snapshots, recorded patch and QA hashes, no execution/repair/approval keys, deep-frozen metadata."""

import dataclasses

import pytest

from siamang.analyst import SystemAnalysis
from siamang.planner import Plan
from siamang.programmer import Patch
from siamang.qa import QAResult
from siamang.reviewer import ReviewRequest
from siamang.reviewer.errors import ReviewerErrorCode, ReviewRequestError
from siamang.reviewer.request import REVIEWER_KEYS
from tests.support.programmer import make_setup
from tests.support.reviewer import chain, review_request


@pytest.fixture
def setup(db, tmp_path):
    return make_setup(db, tmp_path)


@pytest.fixture
def artifacts(setup):
    return chain(setup)


def test_valid_request(setup, artifacts):
    patch, qa = artifacts
    r = review_request(setup, patch, qa, metadata={"ticket": {"id": "RV-1", "tags": ["a"]}})
    d = r.to_dict()
    assert d["patch_id"] == patch.patch_id and d["patch_hash"] == patch.content_hash
    assert d["qa_id"] == qa.qa_id and d["qa_hash"] == qa.content_hash
    assert d["plan_id"] == setup.plan.plan_id and d["analysis_id"] == setup.analysis.analysis_id


def test_request_is_deeply_immutable(setup, artifacts):
    original = {"ticket": {"tags": ["a"]}}
    r = review_request(setup, *artifacts, metadata=original)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.qa_hash = "x"
    for mutate in (lambda: r.metadata.__setitem__("x", 1), lambda: r.metadata["ticket"].__setitem__("x", 1),
                   lambda: r.metadata["ticket"]["tags"].append("b")):
        with pytest.raises((TypeError, AttributeError)):
            mutate()
    original["ticket"]["tags"].append("b")  # the caller's own object is copied, never shared
    original["added"] = 1
    assert r.to_dict()["metadata"] == {"ticket": {"tags": ["a"]}}


@pytest.mark.parametrize("key", ["fix", "repair", "retry", "regenerate", "review", "approve", "reject", "apply",
                                 "merge", "deploy", "commit", "push", "verdict", "status", "qa_status", "override",
                                 "skip_qa", "accept", "command", "shell", "programmer", "loop", "agent_id", "tool",
                                 "model_id", "path", "private_key", "pytest", "execute"])
def test_forbidden_metadata_keys_at_any_depth(setup, artifacts, key):
    for value in ({key: 1}, {"a": {key: 1}}, {"a": [{"b": [{key.upper(): 1}]}]}):
        with pytest.raises(ReviewRequestError) as info:
            review_request(setup, *artifacts, metadata=value)
        assert info.value.code is ReviewerErrorCode.REQUEST_INVALID
    assert {"approve", "apply", "merge", "repair", "retry", "verdict"} <= REVIEWER_KEYS


def _live(obj, cls):
    return type(f"Live{cls.__name__}", (cls,), {})(**{f.name: getattr(obj, f.name) for f in dataclasses.fields(cls)
                                                       if f.init})


def test_snapshots_must_be_exact(setup, artifacts):
    patch, qa = artifacts
    for over in ({"patch": _live(patch, Patch)}, {"patch": patch.to_dict()}, {"patch": None},
                 {"plan": _live(setup.plan, Plan)}, {"plan": setup.plan.to_dict()},
                 {"analysis": _live(setup.analysis, SystemAnalysis)}, {"analysis": object()},
                 {"qa_result": _live(qa, QAResult)}, {"qa_result": qa.to_dict()}, {"qa_result": None}):
        over = dict(over)
        with pytest.raises(ReviewRequestError):
            review_request(setup, over.pop("patch", patch), over.pop("qa_result", qa), patch_hash=patch.content_hash,
                           qa_hash=qa.content_hash, **over)


@pytest.mark.parametrize("field", ["patch_hash", "qa_hash"])
@pytest.mark.parametrize("value", ["", "sha256:abc", "md5:" + "0" * 64, "sha256:" + "G" * 64, "SHA256:" + "0" * 64,
                                   None, 42, "sha256:" + "0" * 64 + "\n"])
def test_hash_format(setup, artifacts, field, value):
    patch, qa = artifacts
    data = dict(task_id=setup.world.task.task_id, project_id=setup.world.project.project_id, plan=setup.plan,
                analysis=setup.analysis, patch=patch, patch_hash=patch.content_hash, qa_result=qa,
                qa_hash=qa.content_hash)
    data[field] = value
    with pytest.raises(ReviewRequestError):
        ReviewRequest(**data)


def test_hashes_are_required(setup, artifacts):
    patch, qa = artifacts
    with pytest.raises(TypeError):
        ReviewRequest(setup.world.task.task_id, setup.world.project.project_id, setup.plan, setup.analysis, patch,
                      patch.content_hash, qa)


@pytest.mark.parametrize("over", [{"task_id": "tsk_../x"}, {"task_id": ""}, {"project_id": "prj x"},
                                  {"project_id": None}, {"metadata": "x"}, {"metadata": {"k": object()}},
                                  {"metadata": {"k": "x" * 20_000}}])
def test_invalid_fields(setup, artifacts, over):
    with pytest.raises(ReviewRequestError):
        review_request(setup, *artifacts, **over)
