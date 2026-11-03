"""Health and validation checks. Pure functions returning results; the CLI renders them.

Nothing here calls an LLM or the network. The only external tool is
``git version`` (Phase 08 Git capability check), which never fails health:
Git is an optional capability, so a missing or too-old Git is ``SKIP``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from siamang.bus import EventBus
from siamang.config import Config, ConfigError, load_config
from siamang.contracts import PAYLOADS, ContractError, Envelope, EventType, is_supported, new_event
from siamang.contracts.examples import load_examples
from siamang.contracts.schema import check_spec
from siamang.git import GitClient, GitError
from siamang.models.factory import build_registry
from siamang.storage import Database

OK, FAIL, SKIP = "OK", "FAIL", "SKIP"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _config_check(config_path: str | None) -> tuple[Check, Config | None]:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        return Check("Configuration", FAIL, str(exc)), None
    return Check("Configuration", OK, config.source), config


def check_storage(config: Config) -> Check:
    parent = Path(config.storage.path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check("Storage", FAIL, f"cannot create {parent}: {exc.strerror or exc}")
    if not os.access(parent, os.W_OK):
        return Check("Storage", FAIL, f"{parent} is not writable")
    return Check("Storage", OK, str(parent))


def check_database(path: Path | str, name: str = "Database") -> Check:
    try:
        with Database(path) as db:
            problems = db.check()
            version = db.schema_version()
    except Exception as exc:  # StorageError, sqlite3.Error, OSError
        return Check(name, FAIL, f"{type(exc).__name__}: {exc}")
    if problems:
        return Check(name, FAIL, "; ".join(problems))
    return Check(name, OK, f"schema v{version}")


def check_event_bus() -> Check:
    try:
        bus = EventBus()
        received: list[Envelope] = []
        sub = bus.subscribe(EventType.TASK_PROGRESS, received.append)
        probe = new_event(
            EventType.TASK_PROGRESS,
            {"summary": "health probe"},
            request_id="req_healthprobe",
            task_id="tsk_healthprobe",
            seq=1,
        )
        delivered = bus.publish(probe)
        bus.unsubscribe(sub)
        after = bus.publish(probe)
    except Exception as exc:
        return Check("Event Bus", FAIL, f"{type(exc).__name__}: {exc}")
    if delivered != 1 or after != 0 or received != [probe]:
        return Check("Event Bus", FAIL, "publish/subscribe/unsubscribe probe mismatch")
    return Check("Event Bus", OK, "publish/subscribe/unsubscribe")


def check_models(config: Config) -> Check:
    """Build the model registry from config. Does not probe providers or call any model."""
    try:
        registry = build_registry(config)
    except Exception as exc:  # RegistryError, ValueError
        return Check("Models", FAIL, f"{type(exc).__name__}: {exc}")
    models = registry.list_models()
    enabled = sum(m.enabled for m in models)
    return Check("Models", OK, f"{len(registry.providers())} provider(s), {len(models)} model(s), {enabled} enabled")


def check_git() -> Check:
    """Optional capability: core stays healthy without Git (SKIP, never FAIL)."""
    try:
        major, minor = GitClient().ensure_supported()
    except GitError as exc:
        return Check("Git", SKIP, f"capability unavailable: {exc.code}")
    return Check("Git", OK, f"git {major}.{minor}")


def check_tools() -> Check:
    """Build the tool catalog (definitions only): no database, Git, or invocation."""
    try:
        from siamang.tools import ActorType, Permission, ToolContext, build_tool_registry

        registry = build_tool_registry()
        tools = registry.list_tools(ToolContext(ActorType.SYSTEM, "validate", {Permission.TOOL_LIST}))
    except Exception as exc:  # ToolError, ValueError
        return Check("Tools", FAIL, f"{type(exc).__name__}: {exc}")
    if any(t.mutating for t in tools):
        return Check("Tools", FAIL, "a mutating tool is registered")
    return Check("Tools", OK, f"{len(tools)} read-only tool(s)")


def check_agents() -> Check:
    """Build the agent catalog. Never invokes an agent, model, tool, or Git, and creates nothing."""
    try:
        from siamang.agents import build_agent_registry

        agents = build_agent_registry().list()
    except Exception as exc:  # AgentError, ValueError
        return Check("Agents", FAIL, f"{type(exc).__name__}: {exc}")
    reference = sum(1 for a in agents if a.metadata.get("reference") is True)
    return Check("Agents", OK, f"{len(agents)} agent(s), {reference} reference, no specialized agents")


def check_orchestrator() -> Check:
    """Check the orchestration policy. Never builds a task, opens the database, or runs an agent."""
    try:
        from siamang.orchestrator import DEFAULT_POLICY

        policy = DEFAULT_POLICY.to_dict()
    except Exception as exc:  # ValueError, ImportError
        return Check("Orchestrator", FAIL, f"{type(exc).__name__}: {exc}")
    if policy["allowed_task_statuses"] != ["running"] or policy["agents_per_orchestration"] != 1:
        return Check("Orchestrator", FAIL, "policy must admit running tasks and one agent only")
    return Check("Orchestrator", OK, "one agent per running task; no task changes, retries, or chaining")


def check_planner() -> Check:
    """Validate the template plan in memory. Never reads a task, opens the database, or executes anything."""
    try:
        from siamang.planner import DEFAULT_POLICY, Plan
        from siamang.planner.strategy import template_steps

        steps = template_steps()
        DEFAULT_POLICY.check_steps(steps)
        Plan("pln_validate", "tsk_validate", "prj_validate", "validate", steps)
    except Exception as exc:  # PlanningError, ValueError
        return Check("Planner", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("Planner", OK, f"template.basic: {len(steps)} acyclic steps; decomposition only")


def check_analyst() -> Check:
    """Validate the analysis domain in memory. Never reads a project, runs Git, or opens the database."""
    try:
        from siamang.analyst import DEFAULT_POLICY, KNOWN_STRATEGIES, Finding, SystemAnalysis

        if set(KNOWN_STRATEGIES) != {"analysis.basic"}:
            return Check("Analyst", FAIL, "unexpected analysis strategies")
        policy = DEFAULT_POLICY.to_dict()
        if policy["writes"] or policy["follows_symlinks"] or policy["reads_secret_files"]:
            return Check("Analyst", FAIL, "analysis policy must be read-only")
        finding = Finding("finding_01", "structure", "info", "validate", "validate", ("README.md exists",))
        SystemAnalysis("ana_validate", "tsk_validate", "prj_validate", "validate", "validate", findings=(finding,))
    except Exception as exc:  # AnalysisError, ValueError
        return Check("Analyst", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("Analyst", OK, "analysis.basic; read-only, bounded, no symlinks or secret files")


def check_programmer() -> Check:
    """Validate the programming domain in memory. Never reads a project, calls a model, or writes anything."""
    try:
        from siamang.programmer import DEFAULT_POLICY, KNOWN_STRATEGIES, Patch, ProgrammingResult, ProposedChange

        if set(KNOWN_STRATEGIES) != {"programming.model", "programming.tools"}:
            return Check("Programmer", FAIL, "unexpected programming strategies")
        policy = DEFAULT_POLICY.to_dict()
        if policy["writes"] or policy["applies_patches"] or policy["executes"] or policy["retries"]:
            return Check("Programmer", FAIL, "programming policy must propose only")
        change = ProposedChange("change_01", "src/validate.py", "create", "step_01", "validate",
                                after_content="x = 1\n", before="absent")
        patch = Patch("pch_validate", "tsk_validate", "prj_validate", "validate", (change,))
        ProgrammingResult("prg_validate", "tsk_validate", "prj_validate", "completed", "validate", patch=patch)
    except Exception as exc:  # ProgrammingError, ValueError
        return Check("Programmer", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("Programmer", OK, "programming.model; proposals only, one model call, nothing applied")


def check_qa() -> Check:
    """Validate the QA domain in memory. Never reads a project, executes code, or writes anything."""
    try:
        from siamang.qa import DEFAULT_POLICY, QAFinding, QAResult

        policy = DEFAULT_POLICY.to_dict()
        if policy["executes_code"] or policy["writes"] or policy["uses_models"] or policy["retries"]:
            return Check("QA", FAIL, "QA policy must be static and read-only")
        finding = QAFinding("patch.hash_mismatch", "error", "patch", "validate")
        QAResult("qa_validate", "tsk_validate", "prj_validate", "pch_validate", None, "fail", (finding,))
        QAResult("qa_validate", "tsk_validate", "prj_validate", "pch_validate", "sha256:" + "0" * 64, "pass")
    except Exception as exc:  # QAError, ValueError
        return Check("QA", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("QA", OK, "qa.static; deterministic, read-only, no execution, no model")


def check_reviewer() -> Check:
    """Validate the Reviewer domain in memory. Never reads a project, executes code, or writes anything."""
    try:
        from siamang.reviewer import DEFAULT_POLICY, ReviewFinding, ReviewResult

        policy = DEFAULT_POLICY.to_dict()
        if (policy["reads_project"] or policy["executes_code"] or policy["writes"] or policy["uses_models"]
                or policy["approves"] or policy["applies"] or policy["repairs"] or policy["retries"]):
            return Check("Reviewer", FAIL, "review policy must be static, read-only, and never approve")
        ids = ("rev_validate", "tsk_validate", "prj_validate", "pln_validate", "ana_validate", "pch_validate",
               "sha256:" + "0" * 64, "qa_validate", "sha256:" + "1" * 64)
        failed = ReviewFinding("qa.failed", "error", "verification", "validate")
        ReviewResult(*ids, "fail", "fail", (failed,))
        ReviewResult(*ids, "pass", "pass")
    except Exception as exc:  # ReviewerError, ValueError
        return Check("Reviewer", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("Reviewer", OK, "review.static; deterministic, read-only, no model, never approves")


def check_loop() -> Check:
    """Validate the Loop domain in memory. Never reads a project, calls a model or service, or writes anything."""
    try:
        from siamang.loop import DEFAULT_POLICY, LoopIteration, LoopPolicy

        policy = DEFAULT_POLICY.to_dict()
        if (policy["applies_patches"] or policy["approves"] or policy["executes_code"] or policy["writes"]
                or policy["persists"] or policy["max_iterations"] > 5 or "pass" in policy["retry_statuses"]):
            return Check("Loop", FAIL, "loop policy must be bounded, read-only, and never approve or apply")
        for unbounded in (0, -1, None, float("inf")):
            try:
                LoopPolicy(max_iterations=unbounded)
            except ValueError:
                continue
            return Check("Loop", FAIL, "loop policy accepted an unbounded iteration limit")
        LoopIteration(1, "tsk_validate", "prj_validate", "programmer_waiting", "prg_validate", "waiting")
    except Exception as exc:  # LoopError, ValueError
        return Check("Loop", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("Loop", OK, "loop.bounded; at most 5 rounds, nothing applied or approved")


def check_approval() -> Check:
    """Validate the Approval domain in memory. Never reads a project, decides anything, or writes anything."""
    try:
        from siamang.approval import DEFAULT_POLICY, ApprovalDecision, ApprovalPolicy
        from siamang.approval.errors import ApprovalError

        policy = DEFAULT_POLICY.to_dict()
        if (policy["authorized_approvers"] or policy["overrides"] or policy["applies_patches"]
                or policy["executes_code"] or policy["writes"] or policy["persists"]):
            return Check("Approval", FAIL, "approval policy must name no default approver and never override or apply")
        for wildcard in ("*", "", "ALL"):
            try:
                ApprovalPolicy(authorized_approvers={wildcard})
            except ValueError:
                continue
            return Check("Approval", FAIL, "approval policy accepted a wildcard approver")
        ApprovalDecision("approve", "validate")
        try:
            ApprovalDecision("reject", "validate")
        except ApprovalError:
            pass
        else:
            return Check("Approval", FAIL, "a rejection without a reason was accepted")
    except Exception as exc:  # ApprovalError, ValueError
        return Check("Approval", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("Approval", OK, "explicit decisions only; no default approver, override, or application")


def check_contract_schemas(config: Config | None = None) -> Check:
    problems = []
    if config is not None and not is_supported(config.contract.version):
        problems.append(f"configured version {config.contract.version} unsupported")
    for etype in EventType:
        if etype not in PAYLOADS:
            problems.append(f"no payload schema for {etype}")
        else:
            problems.extend(check_spec(PAYLOADS[etype], str(etype)))
    if problems:
        return Check("Contract", FAIL, "; ".join(problems))
    return Check("Contract", OK, f"v{config.contract.version if config else '0.1'}, {len(PAYLOADS)} event types")


def check_examples() -> Check:
    try:
        examples = load_examples()
    except Exception as exc:
        return Check("Event fixtures", FAIL, f"cannot load examples: {exc}")
    failures = []
    for name, data in examples.items():
        try:
            env = Envelope.from_dict(data)
            again = Envelope.from_json(env.to_json())
        except ContractError as exc:
            failures.append(f"{name}: {exc}")
            continue
        if env.to_dict() != data or again != env:
            failures.append(f"{name}: round-trip changed the message")
    if not examples:
        failures.append("no examples found")
    if failures:
        return Check("Event fixtures", FAIL, "; ".join(failures))
    return Check("Event fixtures", OK, f"{len(examples)} examples")


def check_message_file(path: Path) -> Check:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Check(str(path), FAIL, exc.strerror or str(exc))
    try:
        env = Envelope.from_json(text)
    except ContractError as exc:
        return Check(str(path), FAIL, str(exc))
    return Check(str(path), OK, str(env.type))


def health(config_path: str | None) -> list[Check]:
    cfg_check, config = _config_check(config_path)
    checks = [cfg_check]
    if config is None:
        checks += [Check(n, SKIP, "configuration failed") for n in ("Storage", "Database")]
    else:
        storage = check_storage(config)
        checks.append(storage)
        if storage.failed:
            checks.append(Check("Database", SKIP, "storage failed"))
        else:
            checks.append(check_database(config.storage.path))
    checks.append(check_event_bus())
    checks.append(check_models(config) if config is not None else Check("Models", SKIP, "configuration failed"))
    checks.append(check_contract_schemas(config))
    checks.append(check_git())
    return checks


def validate(config_path: str | None, files: list[Path]) -> list[Check]:
    cfg_check, config = _config_check(config_path)
    checks = [cfg_check]
    # Schema built from scratch must be correct.
    checks.append(check_database(":memory:", name="Database schema (fresh)"))
    # The configured database, only if it already exists (validate never creates it).
    if config is not None:
        db_path = Path(config.storage.path)
        if db_path.exists():
            checks.append(check_database(db_path, name="Database schema (configured)"))
        else:
            checks.append(Check("Database schema (configured)", SKIP, f"{db_path} does not exist yet"))
    checks.append(check_contract_schemas(config))
    if config is not None:
        checks.append(check_models(config))
    checks.append(check_tools())
    checks.append(check_agents())
    checks.append(check_orchestrator())
    checks.append(check_planner())
    checks.append(check_analyst())
    checks.append(check_programmer())
    checks.append(check_qa())
    checks.append(check_reviewer())
    checks.append(check_loop())
    checks.append(check_approval())
    checks.append(check_examples())
    checks.extend(check_message_file(p) for p in files)
    return checks
