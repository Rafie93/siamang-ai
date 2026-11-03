"""Analysis strategies: how a snapshot becomes findings. The boundary for future strategies.

An :class:`AnalysisStrategy` receives an immutable :class:`AnalysisContext`:
the request, a frozen Task snapshot, a project view (no root path), the
bounded :class:`ProjectInspector`, the read-only :class:`GitState`, and the
policy. It returns an :class:`AnalysisDraft`. It gets no registry, engine,
database, Git client, tool, model, agent, or orchestrator. Strategies are
bound by ID from the code-owned :data:`KNOWN_STRATEGIES` allowlist.

``analysis.basic`` (the only one) is deterministic and deliberately modest:

* it lists directories breadth-first to ``max_depth``, sorted, and does not
  descend into vendored, build, or VCS directories or follow symlinks;
* it reads only a fixed allowlist of manifest files (``pyproject.toml``,
  ``package.json``, ``composer.json``, ...) and extracts **dependency
  names** only, never values;
* it reports secret-looking files by name only and never opens them;
* it matches the request's words against file and directory *names*;
* it confirms the runtime's ``focus`` paths exist as plain, non-sensitive files;
* it states what it observed, labels conclusions as inferred, and lists what
  it could not establish as unknowns. It does not claim to understand
  application semantics.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from siamang.analyst.component import Component, ComponentKind, component_id
from siamang.analyst.errors import LimitReachedError, PathRejectedError
from siamang.analyst.finding import Confidence, Finding, FindingCategory, Severity, finding_id
from siamang.analyst.git_state import GitState
from siamang.analyst.inspection import Entry, EntryKind, ProjectInspector
from siamang.analyst.policy import AnalysisPolicy
from siamang.analyst.request import AnalysisRequest
from siamang.tasks.definition import Task


@dataclass(frozen=True)
class ProjectView:
    """What a strategy may know about the project besides its files. No filesystem location."""

    project_id: str
    name: str
    project_type: str


@dataclass(frozen=True)
class AnalysisContext:
    request: AnalysisRequest
    task: Task
    project: ProjectView
    inspector: ProjectInspector
    git: GitState
    policy: AnalysisPolicy


@dataclass(frozen=True)
class AnalysisDraft:
    summary: str
    components: tuple[Component, ...] = ()
    findings: tuple[Finding, ...] = ()
    risks: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


class AnalysisStrategy(ABC):
    strategy_id: str = ""
    version: str = ""

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> AnalysisDraft:
        """Deterministic for the same context and project snapshot. No writes, no execution."""


# --- the basic strategy --------------------------------------------------------------

SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", "vendor",
                       "dist", "build", "target", ".idea", ".vscode", ".dart_tool", ".gradle", "Pods", ".next",
                       ".nuxt", "coverage", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache"})
TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "specs", "integration_test"})
# manifest name -> ecosystem it indicates
MANIFESTS: Mapping[str, str] = MappingProxyType({
    "pyproject.toml": "Python", "requirements.txt": "Python", "setup.py": "Python", "Pipfile": "Python",
    "package.json": "JavaScript/Node.js", "pubspec.yaml": "Dart", "composer.json": "PHP", "Cargo.toml": "Rust",
    "go.mod": "Go", "pom.xml": "Java (Maven)", "build.gradle": "JVM (Gradle)", "build.gradle.kts": "JVM (Gradle)",
    "Gemfile": "Ruby", "mix.exs": "Elixir",
})
MANIFEST_SUFFIXES: Mapping[str, str] = MappingProxyType({".csproj": ".NET", ".sln": ".NET"})
READ_MANIFESTS = frozenset({"pyproject.toml", "requirements.txt", "package.json", "composer.json", "pubspec.yaml"})
CONFIG_FILES: Mapping[str, ComponentKind] = MappingProxyType({
    "Dockerfile": ComponentKind.DEPLOYMENT, "docker-compose.yml": ComponentKind.DEPLOYMENT,
    "docker-compose.yaml": ComponentKind.DEPLOYMENT, "compose.yaml": ComponentKind.DEPLOYMENT,
    "Procfile": ComponentKind.DEPLOYMENT, ".gitlab-ci.yml": ComponentKind.DEPLOYMENT,
    "Makefile": ComponentKind.BUILD, "tox.ini": ComponentKind.CONFIGURATION,
    "setup.cfg": ComponentKind.CONFIGURATION, ".editorconfig": ComponentKind.CONFIGURATION,
})
DOC_FILES = frozenset({"README.md", "README.rst", "README.txt", "README"})
# dependency name -> (display name, component kind it suggests)
FRAMEWORKS: Mapping[str, tuple[str, ComponentKind]] = MappingProxyType({
    "vue": ("Vue", ComponentKind.FRONTEND), "react": ("React", ComponentKind.FRONTEND),
    "next": ("Next.js", ComponentKind.FRONTEND), "nuxt": ("Nuxt", ComponentKind.FRONTEND),
    "@angular/core": ("Angular", ComponentKind.FRONTEND), "svelte": ("Svelte", ComponentKind.FRONTEND),
    "express": ("Express", ComponentKind.BACKEND), "@nestjs/core": ("NestJS", ComponentKind.BACKEND),
    "django": ("Django", ComponentKind.BACKEND), "flask": ("Flask", ComponentKind.BACKEND),
    "fastapi": ("FastAPI", ComponentKind.BACKEND), "laravel/framework": ("Laravel", ComponentKind.BACKEND),
    "symfony/framework-bundle": ("Symfony", ComponentKind.BACKEND), "flutter": ("Flutter", ComponentKind.FRONTEND),
    "sqlalchemy": ("SQLAlchemy", ComponentKind.DATABASE), "psycopg2": ("PostgreSQL client", ComponentKind.DATABASE),
    "psycopg": ("PostgreSQL client", ComponentKind.DATABASE), "pg": ("PostgreSQL client", ComponentKind.DATABASE),
    "mysql2": ("MySQL client", ComponentKind.DATABASE), "mongoose": ("MongoDB (Mongoose)", ComponentKind.DATABASE),
    "prisma": ("Prisma", ComponentKind.DATABASE),
})
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.@/-]+)")
_WORD_RE = re.compile(r"[a-z0-9]{4,}")
STOPWORDS = frozenset({"with", "from", "that", "this", "into", "add", "make", "please", "should", "would", "about",
                       "change", "update", "create", "need", "needs", "using", "when", "then", "than", "them",
                       "they", "have", "will", "also", "some", "more", "file", "files", "code", "project",
                       "tambahkan", "untuk", "dengan", "pada", "yang", "agar", "bisa", "buat", "fitur"})
MATCH_TERMS_MAX = 10
EVIDENCE_MAX = 20
SAFE_PATH_MAX = 250


def _safe_path(path: str) -> bool:
    """Can this name appear in evidence? Control/format characters (e.g. RTL overrides) and very long
    paths are omitted and only counted, so one hostile file name cannot break or spoof the analysis."""
    return len(path) <= SAFE_PATH_MAX and not any(unicodedata.category(c).startswith("C") for c in path)


def _dependency_names(name: str, text: str) -> tuple[tuple[str, ...], bool]:
    """Dependency *names* from a manifest (never versions or other values). Returns (names, parsed)."""
    names: set[str] = set()
    try:
        if name == "package.json":
            data = json.loads(text)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                section = data.get(key) if isinstance(data, dict) else None
                if isinstance(section, dict):
                    names |= {k for k in section if isinstance(k, str)}
        elif name == "composer.json":
            data = json.loads(text)
            for key in ("require", "require-dev"):
                section = data.get(key) if isinstance(data, dict) else None
                if isinstance(section, dict):
                    names |= {k for k in section if isinstance(k, str)}
        elif name == "pyproject.toml":
            data = tomllib.loads(text)
            project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
            specs = list(project.get("dependencies", []) or [])
            for extra in (project.get("optional-dependencies") or {}).values():
                specs += list(extra or [])
            for spec in specs:
                m = _NAME_RE.match(spec) if isinstance(spec, str) else None
                if m:
                    names.add(m.group(1))
            poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
            for key in ("dependencies", "dev-dependencies"):
                section = poetry.get(key) if isinstance(poetry, dict) else None
                if isinstance(section, dict):
                    names |= {k for k in section if isinstance(k, str) and k != "python"}
        elif name == "requirements.txt":
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "-")):
                    m = _NAME_RE.match(line)
                    if m:
                        names.add(m.group(1))
        elif name == "pubspec.yaml":  # no YAML parser in the stdlib: recognise a top-level-ish key only
            if any(line.strip() == "flutter:" or line.strip().startswith("flutter:") for line in text.splitlines()):
                names.add("flutter")
    except (ValueError, TypeError, AttributeError, tomllib.TOMLDecodeError):
        return (), False
    return tuple(sorted(n.lower() for n in names)), True


class _Builder:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.components: list[Component] = []

    def finding(self, category, severity, title, description, evidence=(), confidence=Confidence.OBSERVED):
        self.findings.append(Finding(finding_id(len(self.findings) + 1), category, severity, title, description,
                                     tuple(evidence)[:EVIDENCE_MAX], confidence))

    def component(self, name, kind, location, description, technologies=(), evidence=(),
                  confidence=Confidence.OBSERVED):
        self.components.append(Component(component_id(len(self.components) + 1), name, kind, location, description,
                                         tuple(technologies), tuple(evidence)[:EVIDENCE_MAX], confidence))


class BasicStrategy(AnalysisStrategy):
    strategy_id = "analysis.basic"
    version = "1"

    @staticmethod
    def _focus(inspector: ProjectInspector, paths: tuple[str, ...]) -> list[str]:
        found = []
        for path in paths:
            parent = path.rpartition("/")[0] or "."
            try:
                if any(e.path == path and e.kind is EntryKind.FILE and not e.sensitive
                       for e in inspector.list_dir(parent)):
                    found.append(path)
            except (LimitReachedError, PathRejectedError):
                continue
        return found

    def analyze(self, context: AnalysisContext) -> AnalysisDraft:
        inspector, policy = context.inspector, context.policy
        b = _Builder()
        unknowns: list[str] = []
        risks: list[str] = []
        recommendations: list[str] = []
        truncated: list[str] = []

        entries, skipped, symlinks, limit, unusual = self._walk(inspector, policy.max_depth)
        if limit:
            truncated.append("directory listing")
        files = [e for e in entries if e.kind is EntryKind.FILE]
        dirs = [e for e in entries if e.kind is EntryKind.DIRECTORY]
        top_dirs = [e.name for e in dirs if "/" not in e.path]

        # 0. files selected for this task: first, so they are the ones the Programmer is shown
        focus = self._focus(inspector, context.request.focus)
        if focus:
            b.finding(FindingCategory.STRUCTURE, Severity.INFO, "Files selected for this task",
                      "These tracked files exist and were selected for the task; their content was not analyzed here.",
                      focus)

        for path in context.request.scope:
            location = path.rstrip("/")
            if path.endswith("/"):
                b.component(location.rsplit("/", 1)[-1], ComponentKind.UNKNOWN, location,
                            "Directory assigned to this work package; it may not exist yet.",
                            evidence=[f"{location}/ assigned to this work package"], confidence=Confidence.INFERRED)
        files_in_scope = [p for p in context.request.scope if not p.endswith("/")]
        if files_in_scope:
            b.finding(FindingCategory.STRUCTURE, Severity.INFO, "Files assigned to this work package",
                      "These paths are assigned to the package; they may not exist yet.", files_in_scope)

        # 1. structure (observed)
        if top_dirs or files:
            b.finding(FindingCategory.STRUCTURE, Severity.INFO, "Top-level structure observed",
                      f"{len(top_dirs)} top-level directories; {len(entries)} entries listed to depth "
                      f"{policy.max_depth}.", [f"{d}/" for d in top_dirs] or ["(no subdirectories)"])
        else:
            b.finding(FindingCategory.STRUCTURE, Severity.LOW, "Project directory is empty",
                      "No files or directories were found under the project root.", ["(empty)"])
        if unusual:
            b.finding(FindingCategory.SECURITY, Severity.LOW, "Entries with unusual names omitted",
                      "Names with control or formatting characters, or very long paths, are counted but not shown "
                      "or inspected.", [f"{unusual} entries"])
        if skipped:
            b.finding(FindingCategory.STRUCTURE, Severity.INFO, "Generated or vendored directories not inspected",
                      "These directories were listed but not descended into.", sorted(skipped))

        # 2. manifests -> technologies (file observed, technology inferred)
        manifests = [e for e in files if e.path.count("/") <= 1 and (
            e.name in MANIFESTS or any(e.name.endswith(s) for s in MANIFEST_SUFFIXES))]
        by_dir: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}
        for e in manifests:
            ecosystem = MANIFESTS.get(e.name) or next(v for s, v in MANIFEST_SUFFIXES.items() if e.name.endswith(s))
            deps: tuple[str, ...] = ()
            if e.name in READ_MANIFESTS:
                deps, ok, reason = self._read_manifest(inspector, e)
                if reason:
                    if reason == "limit":
                        truncated.append("file reads")
                    unknowns.append(f"{e.path} was not read ({reason}).")
                elif not ok:
                    b.finding(FindingCategory.CONFIGURATION, Severity.LOW, "Manifest could not be parsed",
                              "The file exists but its structure was not recognised; its content is not shown.",
                              [e.path], Confidence.OBSERVED)
            location = e.path.rsplit("/", 1)[0] if "/" in e.path else "."
            by_dir.setdefault(location, []).append((e.path, ecosystem, deps))

        for location in sorted(by_dir):
            items = by_dir[location]
            ecosystems = sorted({eco for _, eco, _ in items})
            deps = sorted({d for _, _, ds in items for d in ds})
            frameworks = [FRAMEWORKS[d] for d in deps if d in FRAMEWORKS]
            kinds = {k for _, k in frameworks if k in (ComponentKind.FRONTEND, ComponentKind.BACKEND)}
            kind = kinds.pop() if len(kinds) == 1 else ComponentKind.APPLICATION
            evidence = [f"{path} exists" for path, _, _ in items]
            b.finding(FindingCategory.TECHNOLOGY, Severity.INFO, f"{', '.join(ecosystems)} indicators",
                      "Manifest files indicate the ecosystem; the runtime architecture is not established.",
                      evidence, Confidence.INFERRED)
            if frameworks:
                b.finding(FindingCategory.DEPENDENCY, Severity.INFO, "Frameworks declared as dependencies",
                          "Declared dependency names suggest these frameworks; declaring is not proof of use.",
                          [f"{d} declared in a manifest under {location}" for d in deps if d in FRAMEWORKS],
                          Confidence.INFERRED)
            name = "root application" if location == "." else location.rsplit("/", 1)[-1]
            b.component(name, kind, location, "Application or package identified by its manifest files.",
                        ecosystems + sorted({n for n, _ in frameworks}), evidence, Confidence.INFERRED)
        if not manifests:
            unknowns.append("No known manifest file was found at the root or one level below; "
                            "the technology stack is unknown.")

        # 3. tests, configuration, documentation (observed by name)
        test_dirs = sorted(e.path for e in dirs if e.name in TEST_DIRS)
        if test_dirs:
            b.finding(FindingCategory.TESTING, Severity.INFO, "Test directories present",
                      "Directories conventionally used for tests exist; their contents were not run or read.",
                      [f"{p}/" for p in test_dirs])
            for p in test_dirs[:5]:
                b.component(f"tests ({p})", ComponentKind.TESTS, p, "Directory conventionally holding tests.",
                            evidence=[f"{p}/ exists"])
        else:
            b.finding(FindingCategory.TESTING, Severity.MEDIUM, "No test directory found",
                      "No directory named tests, test, spec, or __tests__ was found within the inspected depth.",
                      confidence=Confidence.UNKNOWN)
            recommendations.append("Decide how the change will be verified; no test directory was found.")
        configs = sorted((e.path, CONFIG_FILES[e.name]) for e in files if e.name in CONFIG_FILES)
        workflows = sorted(e.path for e in dirs if e.path in (".github/workflows",))
        if configs or workflows:
            b.finding(FindingCategory.CONFIGURATION, Severity.INFO, "Build, deployment, or CI configuration present",
                      "Configuration files exist; their content was not read.",
                      [p for p, _ in configs] + [f"{p}/" for p in workflows])
            for kind in sorted({k for _, k in configs} | ({ComponentKind.DEPLOYMENT} if workflows else set())):
                paths = [p for p, k in configs if k is kind] + ([f"{w}/" for w in workflows]
                                                                if kind is ComponentKind.DEPLOYMENT else [])
                b.component(str(kind), kind, ".", f"{kind} configuration identified by file names.",
                            evidence=[f"{p} exists" for p in paths])
        docs = sorted(e.path for e in files if e.name in DOC_FILES)
        if docs:
            b.component("documentation", ComponentKind.DOCUMENTATION, ".", "Documentation files (not read).",
                        evidence=[f"{p} exists" for p in docs])

        # 4. secret-looking files: names only, never opened
        sensitive = sorted(e.path for e in entries if e.sensitive)
        if sensitive:
            b.finding(FindingCategory.SECURITY, Severity.MEDIUM, "Sensitive configuration files detected",
                      "Files whose names suggest secrets exist. They were not opened; only their names are listed.",
                      sensitive)
            risks.append("Secret-looking files exist in the project directory; keep them out of changes, "
                         "logs, and version control.")
        if symlinks:
            b.finding(FindingCategory.STRUCTURE, Severity.INFO, "Symbolic links not followed",
                      "Symbolic links are listed but never followed or read.", sorted(symlinks))

        # 5. names that match the request (inferred relevance)
        terms = sorted({w for w in _WORD_RE.findall(context.request.instruction.lower()) if w not in STOPWORDS})
        terms = terms[:MATCH_TERMS_MAX]
        matches = sorted(e.path for e in entries if e.kind in (EntryKind.FILE, EntryKind.DIRECTORY)
                         and not e.sensitive and any(t in e.name.lower() for t in terms))
        if matches:
            b.finding(FindingCategory.STRUCTURE, Severity.INFO, "Paths whose names match the request",
                      "File or directory names contain terms from the instruction. Relevance is inferred from "
                      "names only; contents were not inspected.", matches, Confidence.INFERRED)
        else:
            unknowns.append("No inspected file or directory name matches the request's terms; "
                            "relevant code may use other names or lie deeper.")

        # 6. Git (read-only snapshot)
        g = context.git
        if g.available:
            where = "detached HEAD" if g.detached else f"branch {g.branch}"
            state = "unborn" if g.unborn else (g.short_commit or "unknown commit")
            b.finding(FindingCategory.INTEGRATION, Severity.INFO, "Git repository state",
                      f"Repository on {where} at {state}.",
                      [f"branch: {g.branch or '(detached)'}", f"commit: {state}", f"worktrees: {g.worktrees}"]
                      + ([f"remotes: {', '.join(g.remotes)}"] if g.remotes else []))
            if not g.clean:
                counts = ", ".join(f"{k}: {v}" for k, v in sorted(g.counts.items()))
                b.finding(FindingCategory.RISK, Severity.MEDIUM, "Working tree has uncommitted changes",
                          f"{g.changed_total} paths differ from HEAD ({counts}).",
                          [f"changed: {p}" for p in g.changed_paths if _safe_path(p)]
                          or [f"changed: {g.changed_total} paths"])
                risks.append("Uncommitted changes in the working tree may conflict with implementation.")
                recommendations.append("Resolve or record local changes before implementation begins "
                                       "(a person or a later phase decides how).")
            if g.ahead or g.behind:
                b.finding(FindingCategory.INTEGRATION, Severity.LOW, "Branch differs from its upstream",
                          "Compared with the local upstream ref only; nothing was fetched.",
                          [f"upstream: {g.upstream}", f"ahead: {g.ahead}", f"behind: {g.behind}"])
        else:
            unknowns.append(f"Git state is unavailable ({g.reason}).")

        # 7. what was not established
        unknowns.append("Application semantics were not inspected: only directory structure and manifest "
                        "dependency names were read.")
        unknowns.append("Runtime, data, and deployment architecture are unknown.")
        if truncated:
            unknowns.append(f"Inspection stopped at a policy limit ({', '.join(sorted(set(truncated)))}); "
                            "the snapshot is partial.")
        plan = context.request.plan
        if plan is not None:
            step = next((s for s in plan.steps if s.title == "Identify affected components"), None)
            if step is not None:
                recommendations.append(f"Use the components and matching paths above for plan {step.step_id} "
                                       "(Identify affected components).")

        usage = inspector.usage()
        summary = (f"Project '{context.project.name}' ({context.project.project_type}): "
                   f"{len(b.components)} components and {len(b.findings)} findings from {usage['entries_listed']} "
                   f"listed entries and {usage['files_read']} read manifest files. Structure and manifests only.")
        metadata = {"truncated": bool(truncated), **usage, "git_available": g.available,
                    "match_terms": terms}
        return AnalysisDraft(summary, tuple(b.components), tuple(b.findings), tuple(risks), tuple(unknowns),
                             tuple(recommendations), MappingProxyType(metadata))

    @staticmethod
    def _walk(inspector: ProjectInspector, max_depth: int):
        """Breadth-first, sorted, bounded. Returns (entries, skipped dirs, symlinks, hit_limit, unusual names)."""
        entries: list[Entry] = []
        skipped: list[str] = []
        symlinks: list[str] = []
        unusual = 0
        queue = [(".", 0)]
        while queue:
            path, depth = queue.pop(0)
            try:
                listed = inspector.list_dir(path)
            except LimitReachedError:
                return entries, skipped, symlinks, True, unusual
            except PathRejectedError:
                continue  # vanished or replaced since it was listed (snapshot semantics)
            for e in listed:
                if not _safe_path(e.path):
                    unusual += 1  # never echoed, never entered
                    continue
                entries.append(e)
                if e.kind is EntryKind.SYMLINK:
                    symlinks.append(e.path)
                elif e.kind is EntryKind.DIRECTORY:
                    if e.name in SKIP_DIRS:
                        skipped.append(f"{e.path}/")
                    elif depth + 1 < max_depth:
                        queue.append((e.path, depth + 1))
        return entries, skipped, symlinks, False, unusual

    @staticmethod
    def _read_manifest(inspector: ProjectInspector, entry: Entry) -> tuple[tuple[str, ...], bool, str | None]:
        try:
            content = inspector.read_text(entry.path)
        except LimitReachedError:
            return (), False, "limit"
        except PathRejectedError:
            return (), False, "not readable"
        if content.too_large:
            return (), False, "too large"
        if content.binary or content.text is None:
            return (), False, "binary"
        deps, ok = _dependency_names(entry.name, content.text)
        return deps, ok, None


KNOWN_STRATEGIES: Mapping[str, type[AnalysisStrategy]] = MappingProxyType({
    BasicStrategy.strategy_id: BasicStrategy,
})
