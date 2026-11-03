"""Test helpers for the Loop: the real Programmer (scripted fake model), QA, and Reviewer, plus recording fakes."""

from __future__ import annotations

from siamang.loop import LoopRequest, LoopService
from siamang.programmer import Programmer
from siamang.qa import QAService
from siamang.reviewer import ReviewerService
from tests.support.programmer import INSTRUCTION, change, make_model, reply
from tests.support.reviewer import clean_changes

CONCERN = reply()  # modifies auth.py without tests: QA pass, review concern
CLEAN = reply(clean_changes())  # QA pass, review pass


def unrelated(n: int) -> str:
    """A different patch every time that nothing in the task relates to: QA pass (warning), review fail."""
    return reply([change(f"scripts/banana_{n}.py", "create", f"x = {n}\n")])


def scripted(*replies: str):
    """A fake model answering with ``replies`` in order; a call beyond the script fails loudly."""
    queue = list(replies)

    def answer(model, request):
        if not queue:
            raise AssertionError("the model was called more often than scripted")
        return queue.pop(0)

    return make_model(answer)


def cycling(make):
    """A fake model answering make(1), make(2), ... forever (the loop must stop it)."""
    count = [0]

    def answer(model, request):
        count[0] += 1
        return make(count[0])

    return make_model(answer)


class RecordingProgrammer(Programmer):
    """The real Programmer that also records every ProgrammingRequest it receives."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.requests = []

    def program(self, request):
        self.requests.append(request)
        return super().program(request)


class CountingQA(QAService):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = 0

    def verify(self, request):
        self.calls += 1
        return super().verify(request)


class CountingReviewer(ReviewerService):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = 0

    def review(self, request):
        self.calls += 1
        return super().review(request)


def make_loop(setup, model, *, policy=None, qa=None, reviewer=None, programmer=None):
    engine = setup.world.engine
    programmer = programmer or RecordingProgrammer(engine, models=model.router)
    qa = qa or CountingQA(engine)
    reviewer = reviewer or CountingReviewer(engine)
    kw = {} if policy is None else {"policy": policy}
    return LoopService(engine, programmer, qa, reviewer, **kw)


def loop_request(setup, **over) -> LoopRequest:
    data = dict(task_id=setup.world.task.task_id, project_id=setup.world.project.project_id,
                instruction=INSTRUCTION, plan=setup.plan, analysis=setup.analysis)
    data.update(over)
    return LoopRequest(**data)


def run(setup, *replies, policy=None, **over):
    model = scripted(*replies)
    service = make_loop(setup, model, policy=policy)
    return service.run(loop_request(setup, **over)), service, model
