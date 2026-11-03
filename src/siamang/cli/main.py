"""``siamang`` command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from siamang import __version__
from siamang.cli import checks as chk
from siamang.config import ConfigError, LoggingConfig, load_config
from siamang.contracts import CONTRACT_VERSION, SUPPORTED_MAJORS
from siamang.agents.errors import AgentError
from siamang.git.errors import GitError
from siamang.tasks.errors import TaskError
from siamang.tools.errors import ToolError
from siamang.log import configure_logging
from siamang.projects.errors import ProjectError
from siamang.storage import SCHEMA_VERSION, Database, EventStore


def _render(title: str, results: list[chk.Check], ok_word: str, fail_word: str) -> int:
    width = max(len(c.name) for c in results) + 3
    print(title)
    print()
    for c in results:
        line = f"{c.name:<{width}}{c.status}"
        if c.detail and (c.status != chk.OK or c.name == "Configuration"):
            line += f"  ({c.detail})"
        print(line)
    failed = any(c.failed for c in results)
    print()
    print(f"Status: {fail_word if failed else ok_word}")
    return 1 if failed else 0


def cmd_health(args: argparse.Namespace) -> int:
    return _render("Siamang Health", chk.health(args.config), "HEALTHY", "UNHEALTHY")


def cmd_validate(args: argparse.Namespace) -> int:
    return _render("Siamang Validate", chk.validate(args.config, [Path(f) for f in args.files]), "VALID", "INVALID")


def cmd_version(args: argparse.Namespace) -> int:
    majors = ", ".join(str(m) for m in sorted(SUPPORTED_MAJORS))
    print(f"siamang {__version__}")
    print(f"contract {CONTRACT_VERSION} (supported major: {majors})")
    print(f"database schema v{SCHEMA_VERSION}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """List configured providers/models. Read-only; never calls a model.

    ``--check`` also runs each provider's health probe (for the bundled
    ``echo`` adapter this is local and instant).
    """
    from siamang.models.capabilities import format_capabilities
    from siamang.models.factory import build_registry

    config = load_config(args.config)
    registry = build_registry(config)
    providers = registry.providers()
    if not providers:
        print("No model providers configured. See siamang.example.toml ([providers.<id>], [models.<id>]).")
        return 0

    print("Providers")
    print()
    rows = [("ID", "TYPE", "ENABLED", "CREDENTIAL", *(["HEALTH"] if args.check else []))]
    unhealthy = False
    for p in providers:
        ref = config.providers[p.provider_id].api_key_env
        cred = "-" if not ref else f"{ref} ({'set' if p.has_credential else 'not set'})"
        row = [p.provider_id, p.info.type, "yes" if p.info.enabled else "no", cred]
        if args.check:
            h = p.health()
            unhealthy |= not h.usable
            row.append(str(h.status) + (f" ({h.detail})" if h.detail else ""))
        rows.append(tuple(row))
    _table(rows)

    print()
    print("Models")
    print()
    rows = [("ID", "PROVIDER", "PRIORITY", "ENABLED", "CONTEXT", "CAPABILITIES")]
    for m in registry.list_models():
        rows.append(
            (
                m.model_id,
                m.provider_id,
                str(m.priority),
                "yes" if m.enabled else "no",
                str(m.context_window or "-"),
                format_capabilities(m.capabilities),
            )
        )
    _table(rows)
    return 1 if unhealthy else 0


def _table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip())


# --- projects ------------------------------------------------------------------
# Metadata-only operations on the Project Registry. Never touches Git, models,
# the network, or the contents of a project directory.


def _registry(args: argparse.Namespace, *, create: bool):
    from siamang.projects import ProjectRegistry

    config = load_config(args.config)
    db_path = Path(config.storage.path)
    if not create and not db_path.exists():
        return None, None
    db = Database(db_path)
    return db, ProjectRegistry(db)


def _cli_path(value: str) -> str:
    # The registry only accepts absolute paths; the CLI resolves ~ and relative input.
    return os.path.abspath(os.path.expanduser(value))


def _cli_metadata(args: argparse.Namespace):
    from siamang.projects import InvalidProjectError

    if getattr(args, "metadata_json", None) is not None:
        try:
            return json.loads(args.metadata_json)
        except json.JSONDecodeError as exc:
            raise InvalidProjectError("metadata", f"invalid JSON: {exc.msg}") from None
    if getattr(args, "meta", None):
        out = {}
        for item in args.meta:
            key, sep, value = item.partition("=")
            if not sep:
                raise InvalidProjectError("metadata", f"--meta expects key=value, got {item!r}")
            out[key] = value
        return out
    return None


def _print_project(project, as_json: bool) -> None:
    d = project.to_dict()
    if as_json:
        print(json.dumps(d, ensure_ascii=False, sort_keys=True))
        return
    width = max(len(k) for k in d) + 2
    for key, value in d.items():
        if key == "metadata":
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        print(f"{key:<{width}}{value}")


def cmd_projects_list(args: argparse.Namespace) -> int:
    db, registry = _registry(args, create=False)
    status = None if args.all else (args.status or "active")
    projects = []
    if registry is not None:
        with db:
            projects = registry.list(status=status, project_type=args.type)
    if args.json:
        print(json.dumps([p.to_dict() for p in projects], ensure_ascii=False, sort_keys=True))
        return 0
    if not projects:
        print("No projects registered." if status is None else f"No {status} projects.")
        return 0
    rows = [("ID", "NAME", "TYPE", "STATUS", "ROOT")]
    rows += [(p.project_id, p.name, str(p.project_type), str(p.status), p.root_path) for p in projects]
    _table(rows)
    return 0


def cmd_projects_get(args: argparse.Namespace) -> int:
    from siamang.projects import ProjectNotFoundError

    db, registry = _registry(args, create=False)
    if registry is None:
        raise ProjectNotFoundError(f"unknown project: {args.project_id}")
    with db:
        _print_project(registry.get(args.project_id), args.json)
    return 0


def cmd_projects_register(args: argparse.Namespace) -> int:
    db, registry = _registry(args, create=True)
    with db:
        project = registry.register(
            name=args.name,
            root_path=_cli_path(args.path),
            project_type=args.type,
            description=args.description,
            metadata=_cli_metadata(args),
            check_exists=not args.allow_missing,
        )
    _print_project(project, args.json)
    return 0


def cmd_projects_update(args: argparse.Namespace) -> int:
    from siamang.projects.registry import UNSET

    db, registry = _registry(args, create=False)
    if registry is None:
        from siamang.projects import ProjectNotFoundError

        raise ProjectNotFoundError(f"unknown project: {args.project_id}")
    metadata = _cli_metadata(args)
    with db:
        project = registry.update(
            args.project_id,
            name=args.name if args.name is not None else UNSET,
            description=args.description if args.description is not None else UNSET,
            project_type=args.type if args.type is not None else UNSET,
            root_path=_cli_path(args.path) if args.path is not None else UNSET,
            metadata=metadata if metadata is not None else UNSET,
            check_exists=not args.allow_missing,
        )
    _print_project(project, args.json)
    return 0


def _cmd_projects_status(args: argparse.Namespace, action: str) -> int:
    from siamang.projects import ProjectNotFoundError

    db, registry = _registry(args, create=False)
    if registry is None:
        raise ProjectNotFoundError(f"unknown project: {args.project_id}")
    with db:
        project = getattr(registry, action)(args.project_id)
    _print_project(project, args.json)
    return 0


def _add_projects_parser(sub) -> None:
    from siamang.projects import ProjectStatus, ProjectType

    types = [t.value for t in ProjectType]
    p = sub.add_parser("projects", help="project registry: list, get, register, update, archive, restore")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--all", action="store_true", help="include archived projects")
    p.add_argument("--status", choices=[s.value for s in ProjectStatus])
    p.add_argument("--type", choices=types)
    p.set_defaults(func=cmd_projects_list)
    ps = p.add_subparsers(dest="projects_command")

    def common(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = ps.add_parser("list", help="list projects (active by default)")
    common(sp)
    sp.add_argument("--all", action="store_true", help="include archived projects")
    sp.add_argument("--status", choices=[s.value for s in ProjectStatus])
    sp.add_argument("--type", choices=types)
    sp.set_defaults(func=cmd_projects_list)

    sp = ps.add_parser("get", help="show one project")
    common(sp)
    sp.add_argument("project_id")
    sp.set_defaults(func=cmd_projects_get)

    def fields(sp, required: bool):
        sp.add_argument("--name", required=required)
        sp.add_argument("--path", required=required, help="project root directory (~ and relative paths are expanded)")
        sp.add_argument("--type", choices=types, default="other" if required else None)
        sp.add_argument("--description", default="" if required else None)
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--meta", action="append", metavar="KEY=VALUE", help="metadata entry (string value); repeatable")
        g.add_argument("--metadata-json", metavar="JSON", help="metadata as a JSON object")
        sp.add_argument("--allow-missing", action="store_true", help="do not require the root directory to exist")

    sp = ps.add_parser("register", help="register a project (metadata only; nothing in the directory is read)")
    common(sp)
    fields(sp, required=True)
    sp.set_defaults(func=cmd_projects_register)

    sp = ps.add_parser("update", help="update an active project (--meta/--metadata-json replace all metadata)")
    common(sp)
    sp.add_argument("project_id")
    fields(sp, required=False)
    sp.set_defaults(func=cmd_projects_update)

    for action in ("archive", "restore"):
        sp = ps.add_parser(action, help=f"{action} a project")
        common(sp)
        sp.add_argument("project_id")
        sp.set_defaults(func=lambda a, _action=action: _cmd_projects_status(a, _action))


# --- git -----------------------------------------------------------------------
# Read-only views of a registered project's repository. Resolves the project
# through the registry, then asks Git. Never writes, fetches, or calls a model.


def _git(args: argparse.Namespace, method: str, **kwargs):
    from siamang.git import GitWorkspaceManager
    from siamang.projects import ProjectNotFoundError

    db, registry = _registry(args, create=False)
    if registry is None:
        raise ProjectNotFoundError(f"unknown project: {args.project_id}")
    with db:
        return getattr(GitWorkspaceManager(registry), method)(args.project_id, **kwargs)


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _status_summary(status) -> str:
    if status.clean:
        return "yes"
    counts = ", ".join(f"{n} {state}" for state, n in status.counts().items() if n)
    return f"no ({counts})"


def cmd_git_inspect(args: argparse.Namespace) -> int:
    info = _git(args, "inspect")
    if args.json:
        print(json.dumps(info.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    loc, head, st = info.location, info.head, info.status
    upstream = "-"
    if st.upstream:
        upstream = st.upstream + (f" (ahead {st.ahead}, behind {st.behind})" if st.ahead is not None else "")
    rows = [
        ("project_id", info.project_id),
        ("project_root", loc.project_root),
        ("repository_root", loc.repository_root),
        ("relation", str(loc.relation) + (f" ({loc.prefix})" if loc.prefix else "")),
        ("path_aliased", _yn(loc.path_aliased)),
        ("linked_worktree", _yn(loc.is_linked_worktree)),
        ("branch", head.branch or "-"),
        ("commit", head.short_commit or "(none: empty repository)"),
        ("detached", _yn(head.detached)),
        ("clean", _status_summary(st)),
        ("upstream", upstream),
        ("remotes", ", ".join(r.name for r in info.remotes) or "-"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def cmd_git_status(args: argparse.Namespace) -> int:
    st = _git(args, "status")
    if args.json:
        print(json.dumps(st.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    head = st.head
    where = f"branch {head.branch}" if head.branch else "detached HEAD"
    print(f"On {where} ({head.short_commit or 'no commits yet'})")
    if st.clean:
        print("Working tree clean.")
        return 0
    rows = [("STATE", "INDEX", "WORKTREE", "PATH")]
    for e in st.entries:
        path = f"{e.original_path} -> {e.path}" if e.original_path else e.path
        rows.append((str(e.state), e.index_status, e.worktree_status, path))
    _table(rows)
    return 0


def cmd_git_diff(args: argparse.Namespace) -> int:
    diff = _git(args, "diff", cached=args.cached)
    if args.json:
        print(json.dumps(diff.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if not diff.files and not diff.patch:
        print("No changes.")
        return 0
    sys.stdout.write(diff.patch)
    if diff.truncated:
        print("\n[diff truncated]")
    return 0


def cmd_git_worktrees(args: argparse.Namespace) -> int:
    workspaces = _git(args, "worktrees")
    if args.json:
        print(json.dumps([w.to_dict() for w in workspaces], ensure_ascii=False, sort_keys=True))
        return 0
    rows = [("", "ID", "KIND", "BRANCH", "COMMIT", "PATH")]
    for w in workspaces:
        flags = "*" if w.current else ""
        branch = w.branch or ("(detached)" if w.detached else "-")
        extra = "".join(f" [{f}]" for f, on in (("locked", w.locked), ("prunable", w.prunable)) if on)
        rows.append((flags, w.workspace_id, str(w.kind), branch, w.short_commit or "-", w.path + extra))
    _table(rows)
    return 0


def _add_git_parser(sub) -> None:
    p = sub.add_parser("git", help="read-only Git views of a registered project (inspect, status, diff, worktrees)")
    gs = p.add_subparsers(dest="git_command", required=True)
    specs = [
        ("inspect", "repository location, HEAD, branch, clean/dirty, remotes", cmd_git_inspect),
        ("status", "working tree status (whole repository)", cmd_git_status),
        ("diff", "unstaged diff (or --cached: staged diff); never modifies anything", cmd_git_diff),
        ("worktrees", "existing Git worktrees (discovery only)", cmd_git_worktrees),
    ]
    for name, help_text, func in specs:
        sp = gs.add_parser(name, help=help_text)
        sp.add_argument("project_id")
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        if name == "diff":
            sp.add_argument("--cached", action="store_true", help="index vs HEAD instead of work tree vs index")
        sp.set_defaults(func=func)


# --- tasks -----------------------------------------------------------------------
# Task state only. Read commands never create the database. cancel/retry change
# lifecycle state (and need --yes); nothing here executes a task. No `tasks run`.


def _task_engine(args: argparse.Namespace):
    from siamang.projects import ProjectRegistry
    from siamang.tasks import TaskEngine

    config = load_config(args.config)
    db_path = Path(config.storage.path)
    if not db_path.exists():
        return None, None
    db = Database(db_path)
    return db, TaskEngine(db, ProjectRegistry(db))


def _require_engine(args: argparse.Namespace):
    from siamang.tasks import TaskNotFoundError

    db, engine = _task_engine(args)
    if engine is None:
        raise TaskNotFoundError("unknown task", task_id=getattr(args, "task_id", None))
    return db, engine


def _print_task(task, as_json: bool, events=None) -> None:
    d = task.to_dict()
    if events is not None:
        d["events"] = [e.to_dict() for e in events]
    if as_json:
        print(json.dumps(d, ensure_ascii=False, sort_keys=True))
        return
    width = max(len(k) for k in d) + 2
    for key, value in d.items():
        if key == "events":
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        print(f"{key:<{width}}{'-' if value is None else value}")
    for e in events or ():
        print(f"  #{e.seq:<3} {e.timestamp.isoformat()}  {e.type}")


def _task_rows(tasks) -> list[tuple[str, ...]]:
    rows = [("ID", "STATUS", "PRIORITY", "RETRY", "PROJECT", "TITLE")]
    rows += [(t.task_id, str(t.status), str(t.priority), f"{t.retry_count}/{t.max_retries}", t.project_id,
              t.title if len(t.title) <= 60 else t.title[:57] + "...") for t in tasks]
    return rows


def cmd_tasks_list(args: argparse.Namespace) -> int:
    db, engine = _task_engine(args)
    tasks = []
    if engine is not None:
        with db:
            tasks = engine.list(project_id=args.project, status=args.status, limit=args.limit, offset=args.offset)
    if args.json:
        print(json.dumps([t.to_dict() for t in tasks], ensure_ascii=False, sort_keys=True))
        return 0
    if not tasks:
        print("No tasks.")
        return 0
    _table(_task_rows(tasks))
    return 0


def cmd_tasks_get(args: argparse.Namespace) -> int:
    db, engine = _require_engine(args)
    with db:
        task = engine.get(args.task_id)
        events = engine.history(args.task_id) if args.events else None
    _print_task(task, args.json, events)
    return 0


def cmd_tasks_recovery(args: argparse.Namespace) -> int:
    """Startup inspection: active tasks. Changes nothing."""
    db, engine = _task_engine(args)
    report = None
    if engine is not None:
        with db:
            report = engine.recovery()
    if args.json:
        print(json.dumps(report.to_dict() if report else {"running": [], "waiting": [], "queued": []},
                         ensure_ascii=False, sort_keys=True))
        return 0
    if report is None or not (report.running or report.waiting or report.queued):
        print("No active tasks.")
        return 0
    if report.running:
        print("Running (may have been interrupted; decide explicitly: tasks cancel, or fail + retry):")
        _table(_task_rows(report.running))
    for name in ("waiting", "queued"):
        group = getattr(report, name)
        if group:
            print(f"{name.capitalize()}:")
            _table(_task_rows(group))
    return 0


def _confirm(args: argparse.Namespace, action: str) -> bool:
    if args.yes:
        return True
    print(f"error: refusing to {action} {args.task_id} without --yes (state change only; nothing is executed)",
          file=sys.stderr)
    return False


def cmd_tasks_cancel(args: argparse.Namespace) -> int:
    if not _confirm(args, "cancel"):
        return 2
    db, engine = _require_engine(args)
    with db:
        task = engine.cancel(args.task_id, reason=args.reason)
    _print_task(task, args.json)
    return 0


def cmd_tasks_retry(args: argparse.Namespace) -> int:
    if not _confirm(args, "retry"):
        return 2
    db, engine = _require_engine(args)
    with db:
        task = engine.retry(args.task_id)
    _print_task(task, args.json)
    return 0


def _add_tasks_parser(sub) -> None:
    from siamang.tasks import TaskStatus

    def list_args(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--project", metavar="PROJECT_ID")
        sp.add_argument("--status", choices=[s.value for s in TaskStatus])
        sp.add_argument("--limit", type=int, default=100)
        sp.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("tasks", help="task state: list, get, recovery, cancel, retry (nothing is executed)")
    list_args(p)
    p.set_defaults(func=cmd_tasks_list)
    ts = p.add_subparsers(dest="tasks_command")
    sp = ts.add_parser("list", help="list tasks (oldest first)")
    list_args(sp)
    sp.set_defaults(func=cmd_tasks_list)
    sp = ts.add_parser("get", help="show one task")
    sp.add_argument("task_id")
    sp.add_argument("--events", action="store_true", help="include the task's event history")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_tasks_get)
    sp = ts.add_parser("recovery", help="report running/waiting/queued tasks after a restart (read-only)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_tasks_recovery)
    sp = ts.add_parser("cancel", help="cancel a queued/running/waiting task (state change only)")
    sp.add_argument("task_id")
    sp.add_argument("--reason")
    sp.add_argument("--yes", action="store_true", help="confirm the state change")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_tasks_cancel)
    sp = ts.add_parser("retry", help="re-queue a failed task (counts against max_retries; nothing is executed)")
    sp.add_argument("task_id")
    sp.add_argument("--yes", action="store_true", help="confirm the state change")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_tasks_retry)


# --- tools -----------------------------------------------------------------------
# Read-only catalog view. Listing never opens the database, runs Git, or
# invokes a tool; there is deliberately no `tools run` in Phase 09.


def _tool_catalog():
    from siamang.tools import ActorType, Permission, ToolContext, build_tool_registry

    context = ToolContext(ActorType.SYSTEM, "cli", {Permission.TOOL_LIST, Permission.TOOL_INSPECT})
    return build_tool_registry(), context


def cmd_tools_list(args: argparse.Namespace) -> int:
    registry, context = _tool_catalog()
    tools = registry.list_tools(context)
    if args.json:
        print(json.dumps([t.to_dict() for t in tools], ensure_ascii=False, sort_keys=True))
        return 0
    rows = [("ID", "VERSION", "OPERATION", "MUTATING", "PROJECT", "ENABLED", "CAPABILITIES")]
    for t in tools:
        rows.append((t.tool_id, t.version, str(t.operation), _yn(t.mutating), _yn(t.requires_project),
                     _yn(t.enabled), ",".join(sorted(str(c) for c in t.capabilities))))
    _table(rows)
    return 0


def cmd_tools_get(args: argparse.Namespace) -> int:
    registry, context = _tool_catalog()
    d = registry.get_tool(args.tool_id, context).to_dict()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, sort_keys=True))
        return 0
    width = max(len(k) for k in d) + 2
    for key, value in d.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, bool):
            value = _yn(value)
        print(f"{key:<{width}}{value}")
    return 0


def _add_tools_parser(sub) -> None:
    p = sub.add_parser("tools", help="registered tools (read-only catalog; nothing is executed)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_tools_list)
    ts = p.add_subparsers(dest="tools_command")
    sp = ts.add_parser("list", help="list registered tools")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_tools_list)
    sp = ts.add_parser("get", help="show one tool definition")
    sp.add_argument("tool_id")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_tools_get)


# --- agents ----------------------------------------------------------------------
# Read-only catalog view. Listing never opens the database, calls a model,
# invokes a tool, or runs an agent; there is deliberately no `agents run`.


def cmd_agents_list(args: argparse.Namespace) -> int:
    from siamang.agents import build_agent_registry

    agents = build_agent_registry().list()
    if args.json:
        print(json.dumps([a.to_dict() for a in agents], ensure_ascii=False, sort_keys=True))
        return 0
    rows = [("ID", "VERSION", "ROLE", "ENABLED", "CAPABILITIES", "MODEL", "TOOLS")]
    for a in agents:
        rows.append((a.agent_id, a.version, a.role, _yn(a.enabled), ",".join(sorted(map(str, a.capabilities))),
                     ",".join(sorted(map(str, a.model_requirements))) or "-",
                     ",".join(sorted(map(str, a.tool_permissions))) or "-"))
    _table(rows)
    return 0


def cmd_agents_get(args: argparse.Namespace) -> int:
    from siamang.agents import build_agent_registry

    d = build_agent_registry().get(args.agent_id).to_dict()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, sort_keys=True))
        return 0
    width = max(len(k) for k in d) + 2
    for key, value in d.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, bool):
            value = _yn(value)
        print(f"{key:<{width}}{value}")
    return 0


def _add_agents_parser(sub) -> None:
    p = sub.add_parser("agents", help="registered agents (read-only catalog; nothing is executed)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_agents_list)
    ags = p.add_subparsers(dest="agents_command")
    sp = ags.add_parser("list", help="list registered agents")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_agents_list)
    sp = ags.add_parser("get", help="show one agent definition")
    sp.add_argument("agent_id")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_agents_get)


# --- orchestrator ----------------------------------------------------------------
# Read-only description of the orchestration policy. Never opens the database
# or runs an agent; there is deliberately no `orchestrator run|execute|invoke`.


def cmd_orchestrator(args: argparse.Namespace) -> int:
    from siamang.agents import build_agent_registry
    from siamang.orchestrator import DEFAULT_POLICY

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "agents": [a.agent_id for a in build_agent_registry().list() if a.enabled],
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("allowed task status", ",".join(policy["allowed_task_statuses"])),
        ("agents per orchestration", str(policy["agents_per_orchestration"])),
        ("orchestratable agents", ",".join(info["agents"]) or "-"),
        ("changes task state", _yn(policy["transitions_task"])),
        ("retries", _yn(policy["retries"])),
        ("chains agents", _yn(policy["chains_agents"])),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_orchestrator_parser(sub) -> None:
    p = sub.add_parser("orchestrator", help="orchestration policy (read-only; nothing is executed)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_orchestrator)


# --- planner ---------------------------------------------------------------------
# Read-only description of the planning policy and strategies. Never opens the
# database or plans anything; there is deliberately no `planner run|execute|create`.


def cmd_planner(args: argparse.Namespace) -> int:
    from siamang.planner import DEFAULT_POLICY, KNOWN_STRATEGIES, TemplateStrategy

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "strategies": {sid: cls.version for sid, cls in sorted(KNOWN_STRATEGIES.items())},
        "default_strategy": TemplateStrategy.strategy_id,
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("strategy", f"{info['default_strategy']} (deterministic)"),
        ("max steps", str(policy["max_steps"])),
        ("max instruction length", str(policy["max_instruction_length"])),
        ("max step description", str(policy["max_description_length"])),
        ("empty plan allowed", _yn(policy["allow_empty_plan"])),
        ("plannable task status", ",".join(policy["plannable_statuses"])),
        ("executes / changes tasks", "no"),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_planner_parser(sub) -> None:
    p = sub.add_parser("planner", help="planning policy (read-only; nothing is planned or executed)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_planner)


# --- analyst ---------------------------------------------------------------------
# Read-only description of the analysis policy and strategies. Never opens the
# database, reads a project, or runs Git; there is deliberately no
# `analyst run|execute|modify` and no way to inspect an arbitrary path.


def cmd_analyst(args: argparse.Namespace) -> int:
    from siamang.analyst import DEFAULT_POLICY, KNOWN_STRATEGIES, BasicStrategy

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "strategies": {sid: cls.version for sid, cls in sorted(KNOWN_STRATEGIES.items())},
        "default_strategy": BasicStrategy.strategy_id,
        "capabilities": ["project structure (bounded)", "manifest dependency names", "secret-file names (not read)",
                         "read-only Git state", "name matches for the request"],
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("strategy", f"{info['default_strategy']} (deterministic)"),
        ("max files read", str(policy["max_files"])),
        ("max file size", f"{policy['max_file_size']} bytes"),
        ("max total bytes", str(policy["max_total_bytes"])),
        ("max directory entries", str(policy["max_directory_entries"])),
        ("max depth", str(policy["max_depth"])),
        ("max findings / components", f"{policy['max_findings']} / {policy['max_components']}"),
        ("max output", f"{policy['max_output_size']} bytes"),
        ("analyzable task status", ",".join(policy["analyzable_statuses"])),
        ("writes / follows symlinks", "no / no"),
        ("reads secret files", "no (names only)"),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_analyst_parser(sub) -> None:
    p = sub.add_parser("analyst", help="analysis policy (read-only; no project is read or analyzed)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_analyst)


# --- programmer ------------------------------------------------------------------
# Read-only description of the programming policy and strategies. Never opens the
# database, reads a project, or calls a model; there is deliberately no
# `programmer run|apply|write` and no way to change files from the CLI.


def cmd_programmer(args: argparse.Namespace) -> int:
    from siamang.programmer import DEFAULT_POLICY, KNOWN_STRATEGIES, ModelStrategy

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "strategies": {sid: cls.version for sid, cls in sorted(KNOWN_STRATEGIES.items())},
        "default_strategy": ModelStrategy.strategy_id,
        "operations": ["create", "modify", "delete"],
        "model_capabilities": {"required": ["text_generation"], "preferred": ["structured_output"]},
        "capabilities": ["proposed changes (in memory)", "before hash from a read-only snapshot",
                         "model output validated as untrusted data"],
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("strategy", f"{info['default_strategy']} (via ModelRouter)"),
        ("model capabilities", "text_generation (structured_output preferred)"),
        ("model calls per request", f"{policy['model_calls_per_request']} (no retry)"),
        ("operations", ", ".join(info["operations"])),
        ("max changes", str(policy["max_changes"])),
        ("max file size", f"{policy['max_file_size']} bytes"),
        ("max total output", f"{policy['max_total_output']} bytes"),
        ("max context", f"{policy['max_context_files']} files / {policy['max_context_bytes']} bytes"),
        ("programmable task status", ",".join(policy["programmable_statuses"])),
        ("writes / applies patches", "no / no"),
        ("executes generated code", "no"),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_programmer_parser(sub) -> None:
    p = sub.add_parser("programmer", help="programming policy (read-only; nothing is generated or written)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_programmer)


# --- qa --------------------------------------------------------------------------
# Read-only description of the QA policy and checks. Never opens the database,
# reads a project, or runs anything; there is deliberately no `qa run|test|apply`.


def cmd_qa(args: argparse.Namespace) -> int:
    from siamang.qa import DEFAULT_POLICY
    from siamang.qa.result import CHECKS, NOT_VERIFIED
    from siamang.qa.service import STRATEGY, VERSION

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "strategy": STRATEGY,
        "strategy_version": VERSION,
        "checks": list(CHECKS),
        "statuses": ["pass", "fail", "inconclusive"],
        "not_verified": list(NOT_VERIFIED),
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("strategy", f"{STRATEGY} (deterministic, static)"),
        ("checks", ", ".join(CHECKS)),
        ("statuses", "pass, fail, inconclusive"),
        ("max changes", str(policy["max_changes"])),
        ("max file size", f"{policy['max_file_size']} bytes"),
        ("max total bytes", str(policy["max_total_bytes"])),
        ("max findings", str(policy["max_findings"])),
        ("verifiable task status", ",".join(policy["verifiable_statuses"])),
        ("executes code / uses models", "no / no"),
        ("writes / retries", "no / no"),
        ("pass means approved", "no"),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_qa_parser(sub) -> None:
    p = sub.add_parser("qa", help="QA policy and checks (read-only; nothing is verified, run, or written)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_qa)


# --- reviewer --------------------------------------------------------------------
# Read-only description of the review policy and dimensions. Never opens the
# database, reads a project, or reviews anything; there is deliberately no
# `reviewer run|approve|apply|fix`.


def cmd_reviewer(args: argparse.Namespace) -> int:
    from siamang.reviewer import DEFAULT_POLICY
    from siamang.reviewer.result import DIMENSIONS, NOT_VERIFIED
    from siamang.reviewer.service import STRATEGY, VERSION

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "strategy": STRATEGY,
        "strategy_version": VERSION,
        "dimensions": list(DIMENSIONS),
        "statuses": ["pass", "concern", "fail", "inconclusive"],
        "not_verified": list(NOT_VERIFIED),
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("strategy", f"{STRATEGY} (deterministic, static)"),
        ("dimensions", ", ".join(DIMENSIONS)),
        ("statuses", "pass, concern, fail, inconclusive"),
        ("max changes", str(policy["max_changes"])),
        ("max total bytes", str(policy["max_total_bytes"])),
        ("max findings", str(policy["max_findings"])),
        ("reviewable task status", ",".join(policy["reviewable_statuses"])),
        ("reads project / uses models", "no / no"),
        ("writes / executes code", "no / no"),
        ("approves / applies / repairs", "no / no / no"),
        ("pass means approved", "no"),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_reviewer_parser(sub) -> None:
    p = sub.add_parser("reviewer", help="review policy and dimensions (read-only; nothing is reviewed or written)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_reviewer)


# --- loop ------------------------------------------------------------------------
# Read-only description of the bounded correction loop. Never opens the
# database, reads a project, calls a model, or runs a round; there is
# deliberately no `loop run|apply|approve|commit`.


def cmd_loop(args: argparse.Namespace) -> int:
    from siamang.loop import DEFAULT_POLICY
    from siamang.loop.result import NOT_DONE, LoopStatus, StopReason
    from siamang.loop.service import STRATEGY, VERSION

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "strategy": STRATEGY,
        "strategy_version": VERSION,
        "round": ["programmer", "qa", "reviewer"],
        "statuses": [s.value for s in LoopStatus],
        "stop_reasons": [r.value for r in StopReason],
        "not_done": list(NOT_DONE),
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("strategy", f"{STRATEGY} (bounded, deterministic)"),
        ("round", "programmer -> qa -> reviewer"),
        ("statuses", ", ".join(info["statuses"])),
        ("stop reasons", ", ".join(info["stop_reasons"])),
        ("max iterations", str(policy["max_iterations"])),
        ("max calls (prog/qa/rev)",
         f"{policy['max_programmer_calls']} / {policy['max_qa_calls']} / {policy['max_reviewer_calls']}"),
        ("retry on review status", ",".join(policy["retry_statuses"])),
        ("max feedback items / bytes", f"{policy['max_feedback_items']} / {policy['max_feedback_bytes']}"),
        ("applies / approves patches", "no / no"),
        ("executes code / writes", "no / no"),
        ("pass means approved", "no"),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_loop_parser(sub) -> None:
    p = sub.add_parser("loop", help="correction-loop policy (read-only; no round is run, nothing is applied)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_loop)


# --- approval --------------------------------------------------------------------
# Read-only description of the approval policy. Never opens the database,
# reads a project, or decides anything; there is deliberately no `approve`,
# `reject`, `apply`, or `approval grant` command: a decision needs an
# explicit, structured ApprovalDecision from an authorized approver.


def cmd_approval(args: argparse.Namespace) -> int:
    from siamang.approval import DEFAULT_POLICY, ApprovalStatus, Decision
    from siamang.approval.result import NOT_DONE

    info = {
        "policy": DEFAULT_POLICY.to_dict(),
        "decisions": [d.value for d in Decision],
        "statuses": [s.value for s in ApprovalStatus],
        "not_done": list(NOT_DONE),
        "authentication": False,
        "persistence": False,
        "events": False,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    policy = info["policy"]
    rows = [
        ("decisions", "approve, reject (explicit only)"),
        ("statuses", "approved, rejected"),
        ("default approvers", "none (nobody may approve until a policy names approvers)"),
        ("evidence for approve", "QA pass, review pass, loop pass + success, verified chain"),
        ("max reason length", str(policy["max_reason_length"])),
        ("overrides", "none"),
        ("applies / executes / commits", "no / no / no"),
        ("authenticates approvers", "no (approver_id is caller context)"),
        ("persists / emits events", "no"),
    ]
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        print(f"{key:<{width}}{value}")
    return 0


def _add_approval_parser(sub) -> None:
    p = sub.add_parser("approval", help="approval policy (read-only; nothing is approved, rejected, or applied)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_approval)


def _open_store(args: argparse.Namespace) -> tuple[Database, EventStore]:
    config = load_config(args.config)
    db_path = Path(config.storage.path)
    if not db_path.exists():
        raise FileNotFoundError(f"database does not exist: {db_path}")
    db = Database(db_path)
    return db, EventStore(db)


def cmd_events(args: argparse.Namespace) -> int:
    db, store = _open_store(args)
    with db:
        for env in store.list(task_id=args.task, limit=args.limit):
            print(env.to_json())
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    db, store = _open_store(args)
    with db:
        for env in store.replay(args.task_id, after_seq=args.after_seq):
            print(env.to_json())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="siamang", description="Siamang AI core (Phase 3 foundation)")
    parser.add_argument("--config", help="TOML config file (default: $SIAMANG_CONFIG, else built-in defaults)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="check configuration, storage, database, event bus, contract").set_defaults(
        func=cmd_health
    )
    p = sub.add_parser("validate", help="validate config, DB schema, contract schemas, fixtures, and message files")
    p.add_argument("files", nargs="*", help="contract message JSON files to validate")
    p.set_defaults(func=cmd_validate)
    sub.add_parser("version", help="show versions").set_defaults(func=cmd_version)
    p = sub.add_parser("models", help="list configured model providers and models (read-only, no model calls)")
    p.add_argument("--check", action="store_true", help="also run provider health probes")
    p.set_defaults(func=cmd_models)
    _add_projects_parser(sub)
    _add_git_parser(sub)
    _add_tools_parser(sub)
    _add_tasks_parser(sub)
    _add_agents_parser(sub)
    _add_orchestrator_parser(sub)
    _add_planner_parser(sub)
    _add_analyst_parser(sub)
    _add_programmer_parser(sub)
    _add_qa_parser(sub)
    _add_reviewer_parser(sub)
    _add_loop_parser(sub)
    _add_approval_parser(sub)
    p = sub.add_parser("events", help="list stored events (JSON lines, append order)")
    p.add_argument("--task", help="filter by task_id")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_events)
    p = sub.add_parser("replay", help="replay a task's event stream ordered by seq (JSON lines, read-only)")
    p.add_argument("task_id")
    p.add_argument("--after-seq", type=int, default=0)
    p.set_defaults(func=cmd_replay)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        configure_logging(load_config(args.config).logging)
    except ConfigError:
        configure_logging(LoggingConfig(level="WARNING"))  # health/validate report the config error
    try:
        return args.func(args)
    except (ConfigError, FileNotFoundError, ProjectError, GitError, ToolError, TaskError, AgentError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
