# Git Workspace Manager (Phase 08)

**Status:** implemented (`src/siamang/git/`). Runtime is still stdlib-only;
Git is used as an external executable, not a library.

The Git Workspace Manager gives Siamang **read-only, local** awareness of a
registered project's Git repository: whether it is a repository, where its
root is, what HEAD and branch are, whether the work tree is clean, what
changed, and which worktrees exist.

> **This layer does not implement autonomous Git workflows.**
> **This layer does not implement task workspaces.**
> **This layer does not implement agent tools.**
> It never commits, stages, branches, checks out, resets, cleans, stashes,
> merges, rebases, creates or removes worktrees, fetches, pulls, or pushes.
> It calls no model, emits no events, and adds nothing to contract v0.1.

## 1. Layers

```
CLI (siamang git ...)
 │
 ▼
GitWorkspaceManager  (git/workspace.py)
 ├──► ProjectRegistry   project_id -> Project (root_path)
 └──► GitClient         (git/client.py, the only subprocess call site)
        │  argv list, shell=False, allowlist, fixed env, timeout
        ▼
       git executable
```

| Module | Role |
|---|---|
| `git/errors.py` | `GitError` + 12 typed codes, stderr classification, scrubbing |
| `git/client.py` | `GitClient.run(args, cwd=...)`: the execution boundary |
| `git/repository.py` | Frozen result types and pure parsers for `-z`/porcelain output |
| `git/workspace.py` | `GitWorkspaceManager`: project → repository → state; protected repositories |

The Project Registry does not know Git exists (it never imports
`siamang.git`). The Git layer never imports models, the bus, storage,
contracts, or the CLI. Enforced by `tests/unit/test_git_architecture.py`.

## 2. Git client (execution boundary)

`GitClient` is the only module in `src/` that imports `subprocess`
(AST-checked). Every call:

* takes a **structured argument list**; strings, bytes, non-`str` items and
  NUL are rejected. It runs with `shell=False`, `stdin=/dev/null`, and
  `cwd="/"`. The repository is always passed as `-C <path>`, one argument,
  and the path must be absolute, so it can never be read as an option.
* must pass the **read-only allowlist**:

  | Command | Allowed form |
  |---|---|
  | `version` | no arguments |
  | `rev-parse` | any (read-only by nature) |
  | `symbolic-ref` | only reading `HEAD` (`--quiet`, `--short`); two positionals would write |
  | `status` | any |
  | `diff` | not `--output`, `--ext-diff`, `--textconv`, `--no-index`; the client always adds `--no-ext-diff --no-textconv --no-color` |
  | `worktree` | `list` only |
  | `remote` | bare or `-v` only (`remote show`/`update` would contact the remote) |

  Anything else (`push`, `pull`, `fetch`, `clone`, `ls-remote`, `commit`,
  `reset`, `clean`, `stash`, `merge`, `rebase`, `checkout`, `switch`,
  `branch`, `tag`, `config`, `worktree add`, global `-c` options, ...)
  raises `InvalidGitArgumentError` **before** a process starts.
* runs in a **fixed environment**, never the caller's: `PATH` (Git's own
  directory, `/usr/bin`, `/bin`), `LC_ALL=C`, `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`,
  `GIT_OPTIONAL_LOCKS=0`, `GIT_NO_LAZY_FETCH=1`, pagers set to `cat`, and
  askpass helpers empty. There is no `HOME`, token, SSH agent socket,
  `GIT_DIR`, `GIT_WORK_TREE`, or `GIT_INDEX_FILE`. Ignoring user and system
  config also makes output deterministic (aliases, `status.*`, colors,
  `core.quotePath` cannot change it).
* adds hardening options: `--no-pager`, `core.fsmonitor=false`,
  `core.hooksPath=/dev/null`, `credential.helper=` (empty),
  `protocol.allow=never` (every transport refused), `core.quotePath=false`,
  `color.ui=false`, `submodule.recurse=false`.
* is **bounded**: timeout default 10 s, at most 120 s (constructor or
  per-call). Output beyond `max_output` (8 MiB) is truncated, and
  `GitResult.truncated` is set.
* is **logged** as `component=git`, with the subcommand, exit status, and
  duration only. Arguments, paths, and the environment are never logged.

Locating Git: `shutil.which("git")` once, at first use. A different binary can
only be passed by trusted Siamang code (`GitClient(binary=...)`, used by
tests). It is **not** read from configuration or project metadata. No `[git]`
config section was added; nothing needed one.

**Minimum Git version: 2.36** (`worktree list -z`; `GIT_CONFIG_GLOBAL` needs
2.32 and `--path-format` 2.31). It is checked once per client, from `git
version`. An older or unparseable version raises
`UnsupportedGitVersionError` and runs nothing else. Dev machine: 2.51.2.

### Repository config is trusted, with defense in depth

Git reads the repository's own `.git/config`. Commands configured there
(filters, fsmonitor, external diff, textconv, hooks) can only be set by
someone who can already write to that repository. Siamang treats them as the
owner's settings. Even so, it disables the ones it can switch off globally:
hooks, fsmonitor, ext-diff, textconv, credential helpers, and transports.
Tests set each of these to a marker script and check that it never runs.
Clean/smudge filters cannot be disabled generically. A repository with a
locally configured filter is trusted as configured.

## 3. Errors

`GitError(code, message, operation, repository_path, retryable, detail)`.
Raw `FileNotFoundError`, `PermissionError`, `TimeoutExpired`,
`CalledProcessError`, and `OSError` never escape. They are converted, and the
new error is raised **outside** the `except` block, so `__cause__` and
`__context__` are empty.

| Code | When | Retryable |
|---|---|---|
| `git_not_installed` | not on `PATH`, or the binary is missing | no |
| `not_repository` | `not a git repository`, or the path does not exist | no |
| `invalid_repository` | not a work tree (bare repo); HEAD unreadable | no |
| `command_failed` | other non-zero exit | no |
| `command_timeout` | timeout hit (the child is killed) | **yes** |
| `permission_denied` | `Permission denied`, `dubious ownership`, binary not executable | no |
| `invalid_git_argument` | allowlist or argument validation failed (also a `ValueError`) | no |
| `repository_locked` | `index.lock: File exists` / another Git process | **yes** |
| `worktree_error` | worktree-related failure | no |
| `unsupported_git_version` | Git < 2.36, or the version cannot be parsed | no |
| `protected_repository` | the project resolves to a protected repository (§8) | no |
| `unknown_git_error` | unexpected OS error or unparseable Git output | no |

Classification reads stderr (C locale). `message` is fixed classified text
and never includes stderr, paths, or command lines. `detail` holds the first
stderr line, with URL credentials and secret-looking values removed,
capped at 200 characters. `repository_path` is an attribute for the caller,
who supplied it; `str(error)` leaves it out. The CLI prints
`error: <code>: <message> (operation=...)`.

## 4. Repository detection and identity

```python
m = GitWorkspaceManager(registry)
m.is_repository(path)      # bool; bare repos and missing paths -> False
m.repository_root(path)    # str; NotRepositoryError otherwise
m.locate_path(path)        # RepositoryLocation (no protection check)
m.locate(project_or_id)    # RepositoryLocation (protected repositories refused)
```

Detection is one call, made from the path itself, so Git handles nested
paths, `.git` files, worktrees, and discovery:

```
git -C <path> rev-parse --path-format=absolute --show-toplevel --show-prefix --git-dir --git-common-dir
```

Siamang never walks the filesystem looking for `.git`.

`RepositoryLocation`:

| Field | Meaning |
|---|---|
| `project_root` | the registry's `root_path`, **unchanged** |
| `repository_root` | Git's top level of the work tree that contains the project |
| `prefix` | Git's `--show-prefix`: the project root relative to `repository_root`, with no trailing slash; `""` when they are the same |
| `relation` | `root` (prefix empty) or `nested` |
| `path_aliased` | `repository_root + "/" + prefix != project_root`, i.e. the project path reaches the repository through a symlink or another alias |
| `git_dir`, `common_git_dir` | Git's own directories; they differ in a linked worktree (`is_linked_worktree`) |

Repository identity is `common_git_dir` (shared by all worktrees of one
repository) plus `repository_root` for the specific work tree. Remote URLs
are **not** identity.

## 5. Path normalization and symlinks

Siamang performs **no** path resolution:

* The project path is the registry's normalized string (Phase 07: absolute,
  `normpath`, symlinks not resolved). It goes to Git unchanged as `-C`.
* Every path Git returns (top level, git dirs, worktree paths) is Git's own
  absolute path. Git resolves symlinks (`getcwd` after `chdir`). On macOS,
  for example, `/var/...` comes back as `/private/var/...`.
* How the two relate is computed by Git (`--show-prefix`), not by
  `realpath` in Siamang. A symlinked project root is reported as
  `relation=root`, `path_aliased=True`. A symlink into a subdirectory is
  reported as `nested`, `path_aliased=True`.
* Phase 08 does not change the registry's duplicate rules. Two active
  projects that alias the same directory through a symlink can both exist.
  `locate()` shows that they share a `repository_root`. Rejecting them is a
  decision left to whoever needs it (likely Phase 10).

Overlapping (nested) active project roots stay **allowed** (open question
from Phase 07). A nested project is a normal case: a monorepo subdirectory
registered as its own project. Each one reports `relation=nested` and its
`prefix`.

## 6. HEAD, branch, status, diff

```python
m.head(p)    # HeadInfo(commit, short_commit, branch, detached, unborn)
m.status(p)  # WorkingTreeStatus
m.diff(p, cached=False)  # Diff
```

**HEAD** comes from `rev-parse --verify --quiet HEAD^{commit}` and
`symbolic-ref --quiet --short HEAD`:

| Situation | `commit` | `branch` | `detached` | `unborn` |
|---|---|---|---|---|
| on a branch | sha | `main`, `feature/x` | false | false |
| detached | sha | `None` | true | false |
| empty repository | `None` | `main` (unborn) | false | true |

`short_commit` is the first 12 hex characters. Nothing creates or switches
branches.

**Status** comes from `status --porcelain=v2 -z --branch
--untracked-files=normal`:

* It covers the whole repository. Entry paths are **relative to the repository
  root** (also for nested projects; filter with `location.prefix`).
* Each `StatusEntry` has `path`, `original_path` (renames), `index_status`,
  and `worktree_status` (Git's letters: `.`, `M`, `T`, `A`, `D`, `R`, `C`,
  `U`; `?` for untracked), plus `state`, `staged`, and `submodule`.
* `state` ∈ `modified`, `added`, `deleted`, `renamed`, `untracked`,
  `conflicted`. Precedence: unmerged → `conflicted`; rename record →
  `renamed`; index `A` → `added`; any `D` → `deleted`; otherwise
  `modified`.
* `clean` means no entries. **Untracked files make a tree not clean.**
  `counts()` gives a per-state count.
* `upstream`, `ahead`, and `behind` come from Git's `branch.ab` header. They
  compare against the **local** tracking ref; nothing is fetched. All three
  are `None` without an upstream.
* Ignored files are not listed. Untracked directories appear once (`dir/`),
  and Git does the directory walk.

**Diff**: `diff [--cached] --find-renames`, as `--numstat -z` (structured
`DiffFile(path, original_path, added, deleted, binary)`) plus the unified
patch text. `cached=False`: work tree vs index. `cached=True`: index vs HEAD
(this also works in an empty repository). Whole repository; no pathspecs
are accepted. Nothing is applied, staged, or reset.

**Unusual paths.** All parsing uses `-z` output and fixed-field splits with a
bounded `maxsplit`, never `line.split()`. Paths with spaces, quotes,
parentheses, Unicode, `;`, `$()`, backticks, tabs, or newlines come back
byte-exact. Non-UTF-8 bytes survive as surrogate escapes. Tests create such
files (and a project directory named `p; touch X && $(touch X) ...`) and check
that nothing executes.

## 7. Worktrees (discovery only)

```python
m.worktrees(p)  # tuple[Workspace, ...]; the first entry is the main worktree
```

The list comes from `worktree list --porcelain -z` (Git's own metadata; no
filesystem scan).

| `Workspace` field | Meaning |
|---|---|
| `workspace_id` | `wsp_` + 24 hex of SHA-256(`common_git_dir` NUL `path`). **Derived, not stored**; stable while the worktree stays at that path |
| `path`, `repository_root` | worktree path; main worktree path |
| `kind` | `main`, `linked`, `bare` |
| `commit`, `short_commit`, `branch`, `detached` | from Git (an unborn HEAD gives `commit=None`) |
| `locked`, `prunable` | reported only; discovery never prunes or unlocks |
| `current` | this is the worktree that contains the project root |

**Worktree creation is deferred.** `git worktree add` writes to `.git`, may
create a branch, and runs `post-checkout` hooks. That makes it a mutating,
risky tool action. Phase 09 (Tool Registry + Permission) and Phase 10 (Task
Engine) must own it. What Phase 08 leaves for them:

* a boundary where adding one allowlisted command is the only change needed
  at the process level;
* the `Workspace` model and its derived ID;
* the protected-repository guard;
* tests that detect any mutating command reaching Git.

There is no automatic worktree, branch, or task workspace, and no cleanup.

## 8. Protected repositories (ARCHITECTURE §6)

ARCHITECTURE §6: projects never live inside Siamang's own repository.

**Decision (G4).** The rule is about **repositories**, not parent
directories. A project whose Git repository *is* a protected repository is
refused by every project-level operation (`locate`, `head`, `status`, `diff`,
`worktrees`, `remotes`, `inspect`) with `protected_repository`. A project that
merely sits in a directory under one, but is its **own** repository, is allowed.

* Identity marker: a commit that only that history contains, listed in
  `PROTECTED_REPOSITORIES`. Git checks for it with
  `rev-parse --verify --quiet <sha>^{commit}` (lazy fetch disabled). Siamang's
  marker is `5551d4bfdd472e4a1c294ee11eada938b020ef0d` (root commit).
* Clones and linked worktrees of these repositories contain the commit, so
  they are refused too. No path is hard-coded.
* Path-level detection (`is_repository`, `repository_root`, `locate_path`)
  is not guarded; it is plain detection.
* The Project Registry stays Git-agnostic, so registration is **not**
  blocked. The refusal happens the first time the Git layer is asked about
  that project.

## 9. Remotes

`m.remotes(p)` lists remotes from `remote -v`, which only reads local
config. Siamang never contacts a remote. Credentials embedded in URLs
(`https://user:token@host/...`) are removed before the value leaves the
parser, along with any other secret-looking text. scp-style `git@host:path`
is kept as it is. Remotes are metadata, never identity.

## 10. Network

* No command that can contact a remote is in the allowlist: `fetch`,
  `pull`, `push`, `clone`, `ls-remote`, `remote show`/`update`/`prune`,
  `submodule`, `archive`.
* Defense in depth: `protocol.allow=never`, `GIT_NO_LAZY_FETCH=1`
  (partial clones cannot fetch missing objects), `GIT_TERMINAL_PROMPT=0`,
  no credential helper, no SSH agent socket.
* Tests: the existing autouse socket block covers the whole suite. A recorder
  test captures every argv sent to Git during a full workflow and checks
  that each one is an allowlisted read command with `protocol.allow=never`
  and a clean environment.

## 11. CLI

```
siamang git inspect   <project_id> [--json]   # location, HEAD, branch, clean/dirty, upstream, remotes
siamang git status    <project_id> [--json]   # entries: STATE INDEX WORKTREE PATH
siamang git diff      <project_id> [--cached] [--json]
siamang git worktrees <project_id> [--json]   # * marks the project's own worktree
```

Each command resolves the project through the registry, never creates the
database, and exits 1 with `error: ...` on a Git or project error. Nothing
writes, fetches, or calls a model.

**Health.** Git is an **optional capability**. `siamang health` shows
`Git OK`, or `Git SKIP (capability unavailable: <code>)` when
Git is missing or too old. A SKIP never makes health fail. `siamang
validate` does not run Git.

## 12. Persistence

None. There is no schema step, no table, and no second database. Schema
stays **v2**. Git is the source of truth for HEAD, branches, the work tree,
and worktrees, so every call asks Git again. `workspace_id` is derived and
needs no storage.

## 13. Security tests

`test_git_security.py`, `test_git_client.py`, `test_git_architecture.py`:

* AST: no `shell=` other than a literal `False`, and no
  `os.system`/`popen`/`exec*`/`spawn*` anywhere in `src/`. `subprocess` is
  imported only by `git/client.py`, and every `subprocess.run` there has a
  `timeout`.
* Deliberate breaks: flipping `shell=False` → `shell=True`, importing
  `subprocess` in `workspace.py`, dropping the timeout, or allowlisting
  `push` each makes a check fail. Flipping `shell=True` in the real file
  makes 10 tests fail.
* Runtime: `os.listdir`/`scandir`/`walk`, `Path.iterdir`/`glob`/`rglob`,
  `glob.glob`, `os.system`/`popen` are patched to fail, and any open inside
  the project fails. The full manager and CLI workflow still works. A
  `.env`/`credentials.json` secret, a token in the environment, and a token
  in a remote URL never reach the output, logs, or Git's environment.
* The caller's `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` are ignored.
* Read-only: every operation runs twice, and the SHA-256 of every file
  under `.git` plus the work tree listing are unchanged.

## 14. Relationship with later phases

| Phase | What it builds on |
|---|---|
| 09 Tool Registry + Permission | May wrap read operations as tools and add mutating ones (`worktree add`, branch, commit) **behind permission and approval**. Git is not registered as a tool in Phase 08 |
| 10 Task Engine | Task → workspace/branch/worktree mapping, worktree creation and cleanup, `GitError.code`/`retryable` → task errors, possibly rejecting symlink-aliased duplicate projects |
| 11+ Agents | Use the Git layer through tools only; never call `GitClient` directly |
