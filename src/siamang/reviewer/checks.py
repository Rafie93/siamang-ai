"""Deterministic review judgments. Each one only *reads* already-validated artifacts and appends findings.

| Dimension | What it judges (by structure and names only; nothing is executed or read from disk) |
|---|---|
| verification | the QAResult as evidence: QA ``fail`` blocks, ``inconclusive`` keeps the review inconclusive, open QA warnings stay concerns |
| alignment | each changed path relates to the task by name (objective, analysis components, evidence) or lies where the analysis pointed; a patch where *nothing* relates is blocking |
| scope | breadth: number of changes and of top-level directories touched |
| maintainability | very long proposed files, many new files, identical content proposed twice |
| consistency | created files follow the naming style and file types already known for the project |
| risk | deletions, dependency manifests, configuration, public interface files, security-sensitive areas |
| feasibility | empty patch or files, changes attributed to analysis/review steps, code changes without tests when the plan asks for testing |

Every judgment is a finding (error/warning/info), never an exception. None
of them claims runtime or semantic correctness. Findings carry fixed
messages, counts, and safe paths only: no file contents, secrets, or
machine paths.
"""

from __future__ import annotations

import dataclasses

import re
from collections.abc import Iterable
from typing import Any

from siamang.analyst.analysis import SystemAnalysis
from siamang.planner.plan import Plan
from siamang.planner.step import StepRole
from siamang.programmer.change import Operation, ProposedChange
from siamang.qa.checks import scope_roots
from siamang.qa.finding import safe_path
from siamang.qa.result import QAResult, QAStatus
from siamang.reviewer.errors import ReviewResultError
from siamang.reviewer.finding import Category, ReviewFinding, Severity
from siamang.reviewer.policy import ReviewerPolicy

E, W, I = Severity.ERROR, Severity.WARNING, Severity.INFO
WITHHELD = "Finding details withheld (unsafe to display)."
QA_CODES_SHOWN = 20  # at most this many QA finding codes are cited in a finding's metadata

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD_RE = re.compile(r"[a-z0-9]+")
STEM = 5  # words of at least this length match on their first STEM letters ("validate" ~ "validation")
# Structural or generic words: they never make a path "related" to a task on their own.
STOPWORDS = frozenset({
    "src", "lib", "libs", "app", "apps", "main", "index", "init", "test", "tests", "spec", "specs", "util", "utils",
    "common", "core", "shared", "file", "files", "new", "the", "and", "for", "with", "from", "into", "add", "update",
    "fix", "change", "changes", "make", "use", "module", "modules", "package", "packages", "backend", "frontend",
    "server", "client", "public", "static", "assets", "docs", "doc", "readme", "project", "code", "this", "that",
    "should", "must", "will", "when", "not", "are", "has", "have", "via", "per", "all", "any", "script", "scripts",
    "bin", "tool", "tools", "exists", "under", "declared",
    "py", "js", "ts", "jsx", "tsx", "mjs", "cjs", "vue", "php", "rb", "go", "rs", "java", "kt", "css", "scss",
    "html", "json", "yaml", "yml", "toml", "txt", "cfg", "ini", "sql", "sh", "md", "rst", "lock", "xml",
})

DEPENDENCY_FILES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "pipfile.lock", "poetry.lock", "uv.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.json", "composer.lock",
    "go.mod", "go.sum", "cargo.toml", "cargo.lock", "gemfile", "gemfile.lock", "build.gradle", "pom.xml",
})
_REQUIREMENTS_RE = re.compile(r"^requirements[a-z0-9_.-]*\.txt\Z")
CONFIG_FILES = frozenset({
    "dockerfile", "makefile", "procfile", ".gitignore", ".dockerignore", ".gitattributes", ".gitlab-ci.yml",
    ".editorconfig", "tsconfig.json", "settings.py", "manage.py", "nginx.conf", "docker-compose.yml",
    "docker-compose.yaml", "compose.yml", "compose.yaml",
})
CONFIG_EXTENSIONS = frozenset({"ini", "cfg", "conf", "toml", "yaml", "yml", "env", "properties"})
CONFIG_DIRS = frozenset({".github", ".circleci", "config", "configs", "deploy", "deployment", "infra", "k8s",
                         "helm", "terraform", "migrations"})
_CONFIG_NAME_RE = re.compile(r"^[a-z0-9_.-]+\.config\.[a-z]+\Z")  # vite.config.ts, webpack.config.js, ...
INTERFACE_NAMES = frozenset({"__init__.py", "index.js", "index.ts", "index.mjs", "index.cjs", "index.jsx",
                             "index.tsx", "mod.rs", "lib.rs"})
INTERFACE_EXTENSIONS = frozenset({"proto", "graphql", "gql"})
_INTERFACE_RE = re.compile(r"^(?:openapi|swagger)\.[a-z]+\Z|\.d\.ts\Z")
SECURITY_WORDS = frozenset({"auth", "authn", "authz", "oauth", "login", "logout", "signin", "signup", "session",
                            "sessions", "password", "passwords", "passwd", "acl", "acls", "rbac", "crypto",
                            "cipher", "security", "jwt", "csrf", "cors", "sso", "saml", "admin", "token",
                            "tokens", "permission", "permissions"})
SECURITY_PREFIXES = ("authent", "authoriz", "encrypt", "decrypt", "permission", "privilege")
CODE_EXTENSIONS = frozenset({"py", "js", "ts", "jsx", "tsx", "mjs", "cjs", "vue", "svelte", "php", "rb", "go", "rs",
                             "java", "kt", "cs", "c", "cc", "cpp", "h", "hpp", "swift", "scala", "dart"})
TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing"})
_TEST_NAME_RE = re.compile(r"^test_|_test\.|\.test\.|\.spec\.|^conftest\.py\Z")
NON_CHANGE_ROLES = frozenset({StepRole.ANALYSIS, StepRole.REVIEW})


class Findings:
    """Collects findings; a message that cannot be shown safely is replaced, never leaked."""

    def __init__(self) -> None:
        self.items: list[ReviewFinding] = []

    def add(self, code: str, severity: Severity, category: Category, message: str, path: Any = None, *,
            inconclusive: bool = False, **metadata: Any) -> None:
        path = safe_path(path) if path is not None else None
        try:
            finding = ReviewFinding(code, severity, category, message, path, inconclusive, metadata)
        except ReviewResultError:
            finding = ReviewFinding(code, severity, category, WITHHELD, path, inconclusive)
        self.items.append(finding)


# --- names --------------------------------------------------------------------------------------


def words(text: str) -> set[str]:
    """Lower-case words of ``text`` (camelCase split), without stopwords or words under 3 letters. Linear time."""
    return {w for w in _WORD_RE.findall(_CAMEL_RE.sub(" ", text).lower()) if len(w) >= 3 and w not in STOPWORDS}


def _vocabulary(texts: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    exact: set[str] = set()
    for text in texts:
        exact |= words(text)
    return frozenset(exact), frozenset(w[:STEM] for w in exact if len(w) >= STEM)


def _related(path: str, exact: frozenset[str], stems: frozenset[str]) -> bool:
    return any(w in exact or (len(w) >= STEM and w[:STEM] in stems) for w in words(path))


def task_texts(plan: Plan, analysis: SystemAnalysis) -> list[str]:
    """What the task is about: objectives, constraints, and what the analysis observed (names, locations,
    evidence). Generic plan-step descriptions, finding titles, and the patch's own text are not evidence."""
    texts = [plan.objective, analysis.objective, *plan.constraints, *analysis.constraints]
    for c in analysis.components:
        texts += [c.name, c.location, *c.evidence]
    for f in analysis.findings:
        texts += f.evidence
    return texts


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _extension(path: str) -> str | None:
    name = _name(path)
    if "." not in name.strip("."):
        return None
    return name.rsplit(".", 1)[-1].lower()


def _style(path: str) -> str | None:
    """Naming style of a file stem: snake, kebab, camel, pascal, or None (neutral: one lower-case word)."""
    stem = _name(path).split(".", 1)[0]
    if not stem or not stem.isascii() or not stem.replace("_", "").replace("-", "").isalnum():
        return None
    if "_" in stem and "-" not in stem and stem == stem.lower():
        return "snake"
    if "-" in stem and "_" not in stem and stem == stem.lower():
        return "kebab"
    if "_" in stem or "-" in stem:
        return None
    if stem[0].isupper() and stem != stem.upper() and any(c.islower() for c in stem):
        return "pascal"
    if stem[0].islower() and any(c.isupper() for c in stem):
        return "camel"
    return None


def _is_test(path: str) -> bool:
    parts = path.lower().split("/")
    return any(p in TEST_DIRS for p in parts[:-1]) or bool(_TEST_NAME_RE.search(parts[-1]))


def _is_code(path: str) -> bool:
    return _extension(path) in CODE_EXTENSIONS


# --- verification: the QAResult as evidence ------------------------------------------------------


def check_verification(qa: QAResult, out: Findings) -> None:
    codes = sorted({f.code for f in qa.findings})[:QA_CODES_SHOWN]
    if qa.status is QAStatus.FAIL:
        out.add("qa.failed", E, Category.VERIFICATION,
                "QA failed: the change has unresolved verification errors, so the review cannot pass.",
                qa_errors=len(qa.errors), qa_codes=codes)
    elif qa.status is QAStatus.INCONCLUSIVE:
        out.add("qa.inconclusive", W, Category.VERIFICATION,
                "QA was inconclusive: verification is incomplete, so no safe judgment can be made.",
                inconclusive=True, qa_codes=codes)
    else:
        out.add("qa.passed", I, Category.VERIFICATION,
                "QA passed its static checks. This is evidence, not proof of correctness.")
    open_warnings = [f for f in qa.findings if f.severity.value == "warning" and not f.inconclusive]
    if open_warnings:
        out.add("qa.warnings_open", W, Category.VERIFICATION,
                f"QA reported {len(open_warnings)} non-blocking warnings that remain unresolved.",
                qa_codes=sorted({f.code for f in open_warnings})[:QA_CODES_SHOWN])


# --- alignment ------------------------------------------------------------------------------------


def check_alignment(changes: tuple[ProposedChange, ...], plan: Plan, analysis: SystemAnalysis,
                    out: Findings) -> None:
    exact, stems = _vocabulary(task_texts(plan, analysis))
    paths, dirs = scope_roots(analysis)
    unrelated = 0
    for c in changes:
        in_scope = c.path in paths or any(c.path.startswith(d + "/") for d in dirs)
        if in_scope or _related(c.path, exact, stems):
            continue
        unrelated += 1
        out.add("alignment.unrelated_change", W, Category.ALIGNMENT,
                "The changed path neither relates to the task by name nor lies where the analysis pointed "
                "(judged by names only).", c.path)
    if changes and unrelated == len(changes):
        out.add("alignment.unrelated_patch", E, Category.ALIGNMENT,
                "No change in the patch relates to the task, plan, or analysis by name or location.")


# --- scope ----------------------------------------------------------------------------------------


def check_scope(changes: tuple[ProposedChange, ...], policy: ReviewerPolicy, out: Findings) -> None:
    if len(changes) > policy.broad_change_count:
        out.add("scope.broad_change", W, Category.SCOPE,
                f"The patch changes {len(changes)} files; more than {policy.broad_change_count} is a broad change.")
    tops = {c.path.split("/", 1)[0] if "/" in c.path else "." for c in changes}
    if len(tops) > policy.max_directories:
        out.add("scope.directory_spread", W, Category.SCOPE,
                f"The patch spans {len(tops)} top-level directories; more than {policy.max_directories} "
                "suggests scope expansion.")


# --- maintainability ------------------------------------------------------------------------------


def check_maintainability(changes: tuple[ProposedChange, ...], policy: ReviewerPolicy, out: Findings) -> None:
    created = [c for c in changes if c.operation is Operation.CREATE]
    if len(created) > policy.max_new_files:
        out.add("maintainability.many_new_files", W, Category.MAINTAINABILITY,
                f"The patch creates {len(created)} files; more than {policy.max_new_files} widens the code base "
                "without a clearly stated need.")
    seen: dict[str, str] = {}
    for c in changes:
        if c.after_content is None:
            continue
        lines = c.after_content.count("\n") + (0 if c.after_content.endswith("\n") else 1)
        # Only a new file: an edit to a file that was already long is not something the patch caused or can fix.
        if lines > policy.max_file_lines and c.operation is Operation.CREATE:
            out.add("maintainability.large_file", W, Category.MAINTAINABILITY,
                    f"The proposed file has {lines} lines; more than {policy.max_file_lines} is hard to review.",
                    c.path, lines=lines)
        if c.after_hash in seen and c.after_content.strip():
            out.add("maintainability.duplicate_content", W, Category.MAINTAINABILITY,
                    "The proposed content is identical to another file in the same patch.", c.path)
        seen.setdefault(c.after_hash or "", c.path)


# --- consistency ----------------------------------------------------------------------------------


def check_consistency(changes: tuple[ProposedChange, ...], analysis: SystemAnalysis, out: Findings) -> None:
    known_paths, _ = scope_roots(analysis)
    known = set(known_paths) | {c.path for c in changes if c.operation is not Operation.CREATE}
    extensions = {e for e in (_extension(p) for p in known) if e is not None}
    styles: dict[str, dict[str, int]] = {}
    for p in known:
        ext, style = _extension(p), _style(p)
        if ext is not None and style is not None:
            styles.setdefault(ext, {}).setdefault(style, 0)
            styles[ext][style] += 1
    for c in changes:
        if c.operation is not Operation.CREATE:
            continue
        ext = _extension(c.path)
        if ext is not None and extensions and ext not in extensions:
            out.add("consistency.unfamiliar_file_type", W, Category.CONSISTENCY,
                    "The created file's type does not appear among the files known for this project.", c.path)
        style, counts = _style(c.path), styles.get(ext or "", {})
        if style is None or not counts:
            continue
        dominant = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if dominant[0] != style and dominant[1] >= 2 and style not in counts:
            out.add("consistency.naming_style", W, Category.CONSISTENCY,
                    f"The file name style ({style}) differs from known files of the same type ({dominant[0]}).",
                    c.path)


# --- risk -----------------------------------------------------------------------------------------


def _security_sensitive(path: str) -> bool:
    for w in _WORD_RE.findall(_CAMEL_RE.sub(" ", path).lower()):
        if w in SECURITY_WORDS or w.startswith(SECURITY_PREFIXES):
            return True
    return False


def check_risk(changes: tuple[ProposedChange, ...], out: Findings) -> None:
    for c in changes:
        name, parts = _name(c.path).lower(), c.path.lower().split("/")
        if c.operation is Operation.DELETE:
            out.add("risk.deletion", W, Category.RISK,
                    "The change deletes a file; anything depending on it may break.", c.path)
        if name in DEPENDENCY_FILES or _REQUIREMENTS_RE.match(name):
            out.add("risk.dependency_change", W, Category.RISK,
                    "The change touches a dependency manifest or lock file.", c.path)
        elif (name in CONFIG_FILES or _extension(name) in CONFIG_EXTENSIONS or _CONFIG_NAME_RE.match(name)
              or any(p in CONFIG_DIRS for p in parts[:-1])):
            out.add("risk.configuration_change", W, Category.RISK,
                    "The change touches build, deployment, CI, or configuration files.", c.path)
        if name in INTERFACE_NAMES or _extension(name) in INTERFACE_EXTENSIONS or _INTERFACE_RE.search(name):
            out.add("risk.public_interface", W, Category.RISK,
                    "The change touches a module or API interface file other code may depend on.", c.path)
        if _security_sensitive(c.path):
            out.add("security.sensitive_area", W, Category.SECURITY,
                    "The change is in a security-sensitive area (authentication, sessions, permissions, "
                    "cryptography); it deserves careful human attention.", c.path)


# --- feasibility ----------------------------------------------------------------------------------


def check_feasibility(changes: tuple[ProposedChange, ...], plan: Plan, out: Findings) -> None:
    if not changes:
        out.add("feasibility.empty_patch", W, Category.FEASIBILITY,
                "The patch proposes no changes; there is nothing to judge.", inconclusive=True)
        return
    roles = {s.step_id: s.role for s in plan.steps}
    for c in changes:
        if roles.get(c.step_id) in NON_CHANGE_ROLES:
            out.add("feasibility.step_role", W, Category.FEASIBILITY,
                    "The change is attributed to a plan step that describes analysis or review, not change work.",
                    c.path)
        if c.after_content is not None and not c.after_content.strip():
            out.add("feasibility.empty_file", W, Category.FEASIBILITY,
                    "The proposed file content is empty or whitespace only.", c.path)
    wants_tests = any(s.role is StepRole.TESTING for s in plan.steps)
    code = [c for c in changes if c.operation is not Operation.DELETE and _is_code(c.path) and not _is_test(c.path)]
    if wants_tests and code and not any(_is_test(c.path) for c in changes):
        out.add("feasibility.tests_absent", W, Category.FEASIBILITY,
                "The plan includes a testing step, but the patch changes code without adding or changing tests.")


def judge(changes: tuple[ProposedChange, ...], plan: Plan, analysis: SystemAnalysis, qa: QAResult,
          policy: ReviewerPolicy) -> tuple[ReviewFinding, ...]:
    """The complete, ordered, bounded review judgment. Pure: the same artifacts and policy give the same findings.

    The Reviewer uses it to review; anyone holding the artifacts can call it again to confirm a ReviewResult's
    findings are what the Reviewer would produce.
    """
    out = Findings()
    check_verification(qa, out)
    check_alignment(changes, plan, analysis, out)
    check_scope(changes, policy, out)
    check_maintainability(changes, policy, out)
    check_consistency(changes, analysis, out)
    check_risk(changes, out)
    check_feasibility(changes, plan, out)
    items = [dataclasses.replace(f, severity=I) if f.code in policy.advisory_codes and f.severity is W else f for f in out.items]
    return bounded(items, policy.max_findings)


def bounded(findings: list[ReviewFinding], limit: int) -> tuple[ReviewFinding, ...]:
    """Errors first (stable), then cut to ``limit``; a cut is itself an inconclusive finding."""
    order = {E: 0, W: 1, I: 2}
    ranked = sorted(findings, key=lambda f: order[f.severity])
    if len(ranked) <= limit:
        return tuple(ranked)
    note = ReviewFinding("review.findings_truncated", W, Category.SCOPE,
                         f"{len(ranked) - limit + 1} further findings were omitted (policy limit).", inconclusive=True)
    if limit == 1:  # one slot: a blocking error must survive, or the cut makes the review inconclusive
        return (ranked[0],) if ranked[0].blocking else (note,)
    return (*ranked[: limit - 1], note)
