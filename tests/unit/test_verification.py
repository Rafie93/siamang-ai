"""Phase 26: a pass comes from exit codes of real checks, and what was not established is said so."""
import pytest

from siamang.sandbox import RawRun, SandboxPolicy, SandboxService
from siamang.verification import Check, VerificationService, VerificationStatus, parse_command


class Script:
    def __init__(self, *runs, up=True):
        self.runs, self.up, self.argvs = list(runs), up, []

    def available(self):
        return self.up

    def run(self, work, argv, policy):
        self.argvs.append(argv)
        return self.runs.pop(0)


def service(runtime, *checks):
    return VerificationService(SandboxService(runtime, SandboxPolicy("img")), checks)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "a.py").write_text("x")
    return tmp_path


TEST, BUILD = Check("test", ("pytest",)), Check("build", ("make", "build"))


def test_all_checks_passing_is_a_pass(root):
    report = service(Script(RawRun(0, "ok"), RawRun(0, "built")), TEST, BUILD).verify(root, [])
    assert report.passed and [c.status for c in report.checks] == ["passed", "passed"]


def test_a_failed_test_stops_the_build_and_carries_the_output_back(root):
    runtime = Script(RawRun(1, "FAILED test_x\n" * 400))
    report = service(runtime, TEST, BUILD).verify(root, [])
    assert report.status is VerificationStatus.FAILED and not report.passed
    assert [c.status for c in report.checks] == ["failed", "skipped"] and len(runtime.argvs) == 1
    feedback = report.feedback()
    assert feedback["failed_checks"][0]["check"] == "test" and len(feedback["failed_checks"][0]["output_tail"]) <= 2000
    assert report.blockers() == ["test: failed (exit 1)"]


def test_a_timeout_is_a_failure_not_a_pass(root):
    report = service(Script(RawRun(None, "", timed_out=True)), TEST).verify(root, [])
    assert report.status is VerificationStatus.FAILED and report.checks[0].status == "timed_out"


def test_no_checks_is_not_configured_and_not_a_pass(root):
    report = service(Script()).verify(root, [])
    assert report.status is VerificationStatus.NOT_CONFIGURED and not report.passed and report.blockers()


def test_no_engine_is_unavailable_and_not_a_pass(root):
    report = service(Script(up=False), TEST).verify(root, [])
    assert report.status is VerificationStatus.UNAVAILABLE and not report.passed


def test_criteria_are_not_verified_unless_a_check_covers_them(root):
    covered = Check("test", ("pytest",), covers=("Login works",))
    report = service(Script(RawRun(0, "ok")), covered).verify(root, [], ["Login works", "Logout works"])
    assert [c["status"] for c in report.criteria] == ["verified", "not_verified"]
    data = report.to_dict()
    assert data["criteria_verified"] == 1 and data["criteria_total"] == 2 and data["passed"]


def test_a_failing_check_verifies_no_criterion(root):
    covered = Check("test", ("pytest",), covers=("Login works",))
    report = service(Script(RawRun(1, "no")), covered).verify(root, [], ["Login works"])
    assert report.criteria[0]["status"] == "not_verified"


def test_commands_come_as_argv_without_a_shell():
    assert parse_command("test", "python -m pytest -q 'a b'").argv == ("python", "-m", "pytest", "-q", "a b")
    assert parse_command("test", "") is None
    with pytest.raises(ValueError):
        parse_command("test", "pytest 'unclosed")


def test_limits():
    with pytest.raises(ValueError):
        service(Script(), *[TEST] * 5)
    with pytest.raises(TypeError):
        VerificationService(object(), [])
