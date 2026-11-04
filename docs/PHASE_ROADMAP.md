# Siamang: Phase Roadmap

Tujuan akhir, prinsip, dan status komponen ada di [VISION.md](VISION.md).

| Phase | Nama | Status |
|---|---|---|
| 01–04 | Pemisahan repository dan kontrak | Selesai |
| 05 | Siamang Core | Selesai |
| 06 | Model Provider + Model Router | Selesai |
| 07 | Project Registry | Selesai |
| 08 | Git Workspace Manager | Selesai |
| 09 | Tool Registry + Permission | Selesai |
| 10 | Task Engine + Persistence | Selesai |
| 11 | Agent Interface | Selesai |
| 12 | Orchestrator | Selesai |
| 13 | Planner | Selesai |
| 14 | System Analyst | Selesai |
| 15 | Programmer | Selesai |
| 16 | QA | Selesai |
| 17 | Reviewer | Selesai |
| 18 | QA → Programmer → QA Loop | Selesai |
| 19 | Approval System | Selesai |
| 20 | HTTP API dan proposal runtime | Selesai; WebSocket pindah ke Phase 23 |
| 21 | Dokumen agen, proyek GitHub, gerbang desain, paket paralel | Selesai |
| 22 | Uji pada proyek nyata | Berjalan; paket paralel belum pernah dipakai pada task nyata |
| 23 | Event System (WebSocket/SSE) | Belum |
| 24 | Virtual Office | Berjalan, repo terpisah `siamang-virtual-office` |
| **25** | **Execution Sandbox** | Selesai di kode; belum diuji dengan container nyata |
| **26** | **Verification Engine** | Sebagian: test/build di sandbox sudah menentukan lulus; pencocokan acceptance criteria belum |
| **27** | **Tool Executor** | Selesai di kode dan tersambung ke Programmer (`sandbox.tools`); belum diuji dengan container nyata |
| **28** | **Recovery Engine dan loop bertingkat** | Selesai di kode dan tersambung ke runtime (`[recovery]`); berlaku untuk run satu Programmer |
| **29** | **Kebijakan otonomi berbasis risiko** | Belum |
| **30** | **Lead otonom** | Belum |
| **31** | **Context dan Artifact Manager** | Belum |
| **32** | **Beberapa task bersamaan** | Belum |
| **33** | **Rilis open source dan server** | Belum |

## Menuju Evidence-Based Autonomous Software Engineering

Urutan ini disengaja: otonomi baru aman setelah ada bukti eksekusi dan batas izin.

| Phase | Tujuan | Selesai jika |
|---|---|---|
| 25 Execution Sandbox | Container terisolasi per task, tanpa secret operator, jaringan dibatasi, ada batas waktu dan sumber daya | Kode tak tepercaya berjalan dan tidak bisa menjangkau host maupun kredensial |
| 26 Verification Engine | QA menjalankan test dan build di sandbox; hasilnya masuk loop revisi; temuan model Reviewer ikut memengaruhi loop; verifikasi terakhir mencocokkan setiap acceptance criteria | Status "lulus" hanya berasal dari eksekusi nyata |
| 27 Tool Executor | Agen membaca file, menjalankan perintah, dan melihat hasilnya di dalam sandbox, dengan batas jumlah giliran | Programmer memperbaiki kode dari hasil yang ia amati sendiri |
| 28 Recovery Engine | Tiga tingkat: agen mengulang dengan umpan balik, penasihat mengganti pendekatan atau membagi tugas, perencana menyusun ulang sisa pekerjaan. Checkpoint per tahap untuk melanjutkan run. Kompromi dicatat sebagai utang teknis di PR | Run pulih sendiri dari kegagalan yang umum, atau berhenti dengan alasan yang jujur. CI gagal sudah kembali ke Programmer, dalam batas `ci_repair` |
| 29 Kebijakan otonomi | Level 0–3 di [VISION.md](VISION.md), gerbang berdasarkan risiko, keputusan otomatis tercatat seperti keputusan manusia | Perubahan berisiko rendah sampai ke PR tanpa berhenti |
| 30 Lead otonom | Lead memilih langkah berikutnya, menjawab pertanyaan yang ada di spesifikasi, dan meneruskan sisanya ke pemilik | Pemilik hanya ditanya hal yang benar-benar butuh keputusannya |
| 31 Context dan Artifact | Indeks kode, memori antar-run (konvensi dan kegagalan yang ditemukan diteruskan ke tugas berikutnya), penyimpanan hasil build, anggaran desain yang lebih longgar dari 7 KB | Proyek besar tidak kehilangan konteks |
| 32 Task bersamaan | Clone per task, antrean, peninjauan ulang file lock dan SQLite. Pembagian paket berbasis graf dependensi, menggantikan gelombang | Beberapa task berjalan tanpa `worker_busy` |
| 33 Rilis | Lisensi, pengganti template frontend berbayar bila perlu, CI, Docker, `SECURITY.md`, provider API key, GitHub App, TLS, login | Orang lain bisa memasang dengan aman |

## Phase 01–04: Pemisahan repository dan kontrak

Siamang dipisahkan ke repository sendiri, dan batas dengan klien didefinisikan
sebagai kontrak: [CONTRACT.md](CONTRACT.md).

## Phase 05: Siamang Core Foundation (DONE) [lama: 3]

Fondasi tanpa otak dan tangan. Keputusan utamanya ada di
[ARCHITECTURE.md §9](ARCHITECTURE.md#9-keputusan-utama).

Dibangun:

1. Package Python `src/siamang` (≥ 3.11), runtime **stdlib-only**.
2. Config bertipe: defaults < TOML < env (`SIAMANG_<SECTION>_<KEY>`).
3. Logging terpusat: level standar, component, `request_id`/`task_id`/
   `event_id`, console teks + file JSON lines, redaksi secret.
4. Event bus in-process sinkron: `publish`, `subscribe`, `unsubscribe`,
   wildcard `*`.
5. Envelope bertipe + kontrak v0.1 (12 event) + validator + versioning.
6. 10 contoh JSON Phase 2 sebagai fixture kanonik
   (`src/siamang/contracts/examples/`).
7. SQLite schema v1: `tasks`, `events` (append-only via trigger).
8. `TaskStore`, `EventStore` (append, list, `replay(task_id, after_seq)`).
9. CLI: `siamang health | validate [files] | version | events | replay`.
10. 168 test pytest, offline (socket diblok di test), tanpa LLM/API key.

Kriteria selesai (semua terpenuhi):

* Contoh payload di kontrak lolos validator dan round-trip tanpa kehilangan
  informasi.
* Event bisa dipublish → disimpan → di-replay berdasarkan `seq`.
* Tidak ada panggilan LLM atau eksekusi tool.

### Temuan pada kontrak v0.1 (tidak diubah, perlu keputusan)

| # | Temuan | Usulan |
|---|---|---|
| C1 | Contoh §3 envelope ber-`payload: { }` untuk `task.progress`, padahal `summary` wajib | Perbaiki contoh di dokumen (fixture sudah memakai `summary` minimal) |
| C2 | Contoh `approval.required` dan `task.waiting_approval` sama-sama `seq: 6`, padahal `seq` unik per task dan `approval.required` dikirim lebih dulu | Ubah contoh `task.waiting_approval` menjadi `seq: 7` |
| C3 | Kontrak: status `queued`; brief Phase 3: status task `accepted` | Dipetakan (ARCHITECTURE D9). Putuskan apakah `GET /v0/tasks/{id}` memakai `queued` |
| C4 | `error.step` (task.failed) tidak punya tipe | Sementara divalidasi sebagai nilai JSON bebas; tetapkan tipe di v0.2 |
| C5 | `allowed_channels` dan `approval.*.channel` tidak punya enum | Sementara string bebas; pertimbangkan enum saat kanal approval bertambah |
| C6 | Event masuk (`approval.*`, `task.cancel`) tanpa `seq`, sehingga tidak ikut replay | Sesuai kontrak; bila audit butuh urutan lengkap, gunakan `events` (urutan `position`) |

## Phase 06: Model Provider + Model Router (DONE) [lama: 4]

Detail: [MODEL_LAYER.md](MODEL_LAYER.md).

Dibangun: Capability, ModelDefinition, ProviderInfo/Health, ModelRequest/
Response, 10 kode `ModelError`, `Secret` + `api_key_env`, `ModelProvider`
(ABC boundary), `ModelRegistry` (in-memory), `PriorityPolicy`, `ModelRouter`
(select saja), config `[router]`/`[providers.*]`/`[models.*]`, adapter lokal
`echo`, CLI `siamang models [--check]`. 304 test (168 Phase 3 + 136 Phase 06),
offline.

Tidak dibangun (sesuai brief): agent, orchestrator, tool, task event,
REST/WebSocket, adapter vendor komersial, cost/latency
routing, streaming.

### Untuk phase berikutnya

| Phase | Yang masih harus dikerjakan terkait model layer |
|---|---|
| 07 Project Registry | Tidak ada ketergantungan. Model layer tidak tahu soal project |
| 08 Git Workspace | Tidak ada ketergantungan |
| 09 Tool Registry + Permission | `tool_calling` sudah ada sebagai capability dan `FinishReason.TOOL_CALL` dicadangkan, tetapi request/response belum membawa definisi tool atau tool call |
| 10 Task Engine | Memetakan `ModelError.code` ke `task.failed.error.code` kontrak (mis. `provider_unavailable`/`timeout` → `MODEL_UNAVAILABLE`); mencatat `ModelRequest.request_id` per task; memutuskan retry berdasarkan `retryable` |
| 11 Agent Interface | Agent memanggil `router.select()` lalu `selection.generate()`; per-peran capability/priority (mis. model "programmer") belum ada di config |
| 12 Orchestrator | Kebijakan routing per peran/per project (policy baru lewat `RoutingPolicy`); caching health |

## Phase 07: Project Registry (DONE) [lama: 5]

Detail: [PROJECT_REGISTRY.md](PROJECT_REGISTRY.md).

Dibangun: `Project` (typed), `ProjectType`, `ProjectStatus` (active/archived),
validasi field, error bertipe, `ProjectRegistry` (register, get, get_by_name,
find_by_root_path, list, update, archive, restore; tanpa delete),
`ProjectStore`, schema step v2 (tabel `projects`, partial unique index,
trigger no-delete dan immutable identity), upgrade DB v1 → v2 di tempat, CLI
`siamang projects`. 453 test (304 sebelumnya + 149 baru), offline.

Tidak dibangun (sesuai brief): Git, workspace, task engine, agent, model,
tool, event/kontrak baru, API, discovery/auto-registration.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 08 Git Workspace | Apakah `root_path` repo Git; root bersarang; alias symlink; aturan ARCHITECTURE §6 (project tidak di dalam repo Siamang) belum ditegakkan |
| 09 Tool Registry | Izin tool per project belum ada (metadata sengaja tidak memengaruhi izin) |
| 10 Task Engine | Resolusi `project.hint`/`project.id` → `PROJECT_NOT_FOUND`/`PROJECT_AMBIGUOUS`; validasi `tasks.project_id` lewat registry; mengisi `task.accepted.project.{id,name}` |

## Phase 08: Git Workspace Manager (DONE) [lama: 6]

Detail: [GIT_WORKSPACE.md](GIT_WORKSPACE.md).

Dibangun: `GitClient` (satu-satunya pemakai `subprocess`; argv list,
`shell=False`, allowlist read-only, env tetap, timeout, output dibatasi),
`GitError` + 12 kode, `GitWorkspaceManager` (`is_repository`,
`repository_root`, `locate`, `head`, `status`, `diff`/`diff --cached`,
`worktrees`, `remotes`, `inspect`), tipe hasil + parser `-z`, penjaga repo
terlindungi (ARCHITECTURE §6), CLI `siamang git
inspect|status|diff|worktrees`, cek Git opsional di `health`. 658 test (453
sebelumnya + 205 baru), offline. Tidak ada perubahan schema (tetap v2) dan
tidak ada config baru.

Tidak dibangun (sesuai brief): pembuatan worktree/branch, commit, stage,
reset, fetch/pull/push, merge/rebase, Git sebagai tool agent, task
workspace, integrasi model/agent, API GitHub/GitLab, manajemen
kredensial.

Jawaban atas hal terbuka dari Phase 07:

| Hal | Keputusan |
|---|---|
| Apakah `root_path` repo Git | Ditanya ke Git setiap kali (`rev-parse`), tidak disimpan |
| Root bersarang | Diizinkan; `relation=nested` + `prefix` |
| Alias symlink | Siamang tidak me-resolve; Git yang me-resolve; `path_aliased=True`. Duplikat lewat symlink belum ditolak |
| ARCHITECTURE §6 | Per repository (G4). Repo terpisah diizinkan; repo terlindungi (dan clone/worktree-nya) ditolak |

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 09 Tool Registry + Permission | Membungkus operasi Git sebagai tool; operasi mutasi (worktree add, branch, commit) hanya di bawah permission/approval |
| 10 Task Engine | Task → workspace/branch/worktree; pembuatan dan pembersihan worktree; pemetaan `GitError.code`/`retryable` ke error task; mungkin menolak project duplikat lewat symlink |

## Phase 09: Tool Registry + Permission (DONE) [lama: 7]

Detail: [TOOL_REGISTRY.md](TOOL_REGISTRY.md).

Dibangun: `ToolDefinition` (data imutabel, implementasi lewat ID), validasi
input strict, `Permission` (12), `ActorType`, `ToolContext`,
`PermissionPolicy` (plafon per actor, deny by default), `ImplementationTable`
(allowlist method adapter), `ToolRegistry` (register, list/get dengan izin
discovery, `invoke` 10 langkah, `ToolResult`), `ToolError` (14 kode), lima
tool Git read-only (`git.inspect`, `git.status`, `git.diff`,
`git.diff_cached`, `git.worktrees`) lewat `GitWorkspaceManager`, CLI
`siamang tools [list|get]`, cek `Tools` di `validate`. 888 test (658
sebelumnya + 230 baru), offline. Tanpa perubahan schema (tetap v2), tanpa
config baru.

Tidak dibangun (sesuai brief): Task Engine, agent, orchestrator, planner,
approval, API, LLM tool calling, `tools run`, tool mutating
apa pun, tool shell/proses/jaringan/filesystem, persistensi tool/izin/riwayat.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 10 Task Engine | Memberi plafon izin untuk actor `task` di policy; memanggil `ToolRegistry.invoke`; memutuskan persistensi/event hasil tool; resolusi `project.hint` |
| 11 Agent Interface | Plafon izin untuk `agent`; argumen tool buatan model divalidasi lewat `invoke` yang sama |
| 19 Approval | Tool mutating (worktree add, branch, commit) baru boleh ada di belakang approval, setelah `check_read_only` dibuka secara sengaja |
| — (temuan) | `validate_project_id` (Phase 07) dan `contracts.schema.ID_RE` memakai `re.match` + `$`, sehingga ID dengan newline di akhir (`prj_x\n`) diterima. Tool layer memakai `fullmatch`; perbaikan di lapisan bawah menunggu keputusan |

## Phase 10: Task Engine + Persistence (DONE) [lama: 8]

Detail: [TASK_ENGINE.md](TASK_ENGINE.md).

Dibangun: `Task` (imutabel), `TaskRequest` (strict, `source` wajib),
`TaskFailure` (ter-normalisasi, ter-redaksi), `TaskResult`, lifecycle
eksplisit (`queued`/`running`/`waiting`/`completed`/`failed`/`cancelled`,
8 aksi), `TaskRepository` (SQL saja, UPDATE compare-and-set), `TaskEngine`
(create, start, wait, requeue, complete, fail, cancel, retry, get, list,
history, recovery), state + event dalam satu transaksi SQLite, bus setelah
commit, schema **v3** (tabel `tasks` dibangun ulang, migrasi v2 → v3),
kontrak **v0.2** (aditif: `task.waiting`, `task.requeued`,
`task.retry_requested`), CLI `siamang tasks [list|get|recovery|cancel|retry]`.
Perbaikan lapisan bawah: `ID_RE`/`PROJECT_ID_RE`/`METADATA_KEY_RE` memakai
`\Z` (temuan Phase 09). 1225 test (888 sebelumnya + 337 baru), offline,
0 skip.

Tidak dibangun (sesuai brief): eksekusi task, agent, orchestrator, planner,
retry otomatis, kebijakan recovery otomatis, approval, Telegram, mutasi
Git, `tasks run`.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 11 Agent Interface | Agent melaporkan hasil lewat `complete(TaskResult(details=...))` / `fail(TaskFailure(...))`; plafon izin actor `agent` |
| 12 Orchestrator | Mengambil task `queued`, memanggil `start`; memutuskan task `running` setelah crash (laporan `recovery()`); memberi plafon izin actor `task` di Tool Registry; memetakan `GitError`/`ToolError` ke `TaskFailure` |
| 12+ | Task → workspace/branch/worktree; resolusi `project.hint`; semantik subtask untuk `parent_task_id` |
| Integrasi klien | `task.accepted` dengan `duplicate: true` untuk `request_id` berulang (sekarang engine mengembalikan `duplicate=True` tanpa event baru) |

## Phase 11: Agent Interface (DONE) [lama: 9]

Detail: [AGENT_INTERFACE.md](AGENT_INTERFACE.md).

Dibangun: `AgentDefinition` (data imutabel, implementasi lewat ID),
`AgentCapability`, `AgentRequest` (task/project scoped, tanpa field
eksekusi), `AgentContext` (field bertipe, `AgentProject` tanpa `root_path`,
`ModelAccess` lewat ModelRouter, `ToolAccess` lewat ToolRegistry +
PermissionPolicy), `AgentResult` (completed/failed/waiting), `AgentError`
(12 kode), `Agent` (ABC sinkron), allowlist implementasi, `AgentRegistry`
(register/get/exists/list + `invoke` 11 langkah, tolak agent-ke-agent),
agent referensi `agent.echo`, CLI `siamang agents [list|get]`, cek `Agents`
di `validate`. 1503 test (1225 sebelumnya + 278 baru), offline, 0 skip.
Tanpa perubahan schema (tetap v3), kontrak (tetap v0.2), atau config.

Tidak dibangun (sesuai brief): agent spesialis (analyst, programmer, QA,
reviewer, planner), orchestrator, loop agent, pemanggilan agent-ke-agent,
retry otomatis, perubahan state task dari agent, event agent, persistensi
eksekusi, `agents run`, API, Telegram.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 12 Orchestrator | Memilih dan mengurutkan agent; memanggil `TaskEngine.start` lalu `build_context` + `AgentRegistry.invoke`; menerjemahkan `AgentResult` ke `complete`/`fail`/`wait`; mencatat eksekusi sebagai event task; memberi plafon izin actor `agent` (dan `task`) di policy |
| 13–17 Agent spesialis | Satu entri allowlist + definisi per peran; benchmark model per peran sebelum memilih `model_requirements` |
| 19 Approval | `AgentStatus.waiting` + `approval.required` untuk aksi mutating |
| — | Output > 256 KiB (artefak besar) perlu desain tersendiri |

## Phase 12: Orchestrator (DONE) [lama: 10]

Detail: [ORCHESTRATOR.md](ORCHESTRATOR.md).

Dibangun: `OrchestrationRequest` (task, project, satu `agent_id`;
validasi lewat `AgentRequest` + kunci eksekusi tambahan),
`OrchestrationResult` (mencerminkan satu `AgentResult`;
completed/failed/waiting), `OrchestrationError` (12 kode, kode agent
sebagai `cause`), `OrchestrationPolicy` (hanya `running`, satu agent,
allowlist agent opsional), `Orchestrator.execute` (TaskEngine.get →
ProjectRegistry → AgentRegistry.get → `build_context` →
`AgentRegistry.invoke` tepat sekali), guard orkestrasi bertingkat, prefix
ID `orc_`, CLI `siamang orchestrator [--json]` (read-only), cek
`Orchestrator` di `validate`. 1681 test (1503 sebelumnya + 178 baru),
offline, 0 skip. Tanpa perubahan schema (v3), kontrak (v0.2), config,
atau event.

Tidak dibangun (sesuai brief): planner, dekomposisi task, loop otonom,
retry, rantai agent, agent spesialis, transisi task otomatis, persistensi
dan event orkestrasi, `orchestrator run`, approval, API, Telegram.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 13 Planner | Menentukan urutan langkah/agent; memanggil `Orchestrator.execute` per langkah; menerjemahkan `OrchestrationResult` ke transisi task (`complete`/`fail`/`wait`) secara eksplisit |
| 13–18 | Snapshot task dibaca sekali per eksekusi; pembatalan task saat agent berjalan belum diperiksa ulang |
| 19 Approval | Status `waiting` + `approval.required` untuk aksi mutating |
| 20/23 | Riwayat dan event eksekusi (sekarang hanya log) |

## Phase 13: Planner (DONE) [lama: 11]

Detail: [PLANNER.md](PLANNER.md).

Dibangun: `PlanningRequest` (validasi Task Engine + kunci eksekusi
planner), `PlanStep` (ID posisi `step_01`, `StepRole` = jenis kerja),
`Plan` (imutabel, validasi graf: ID unik, dependensi ada, tanpa self dan
siklus, urutan daftar = urutan eksekusi; `content_hash` tanpa `plan_id`),
`PlanningPolicy` (12 langkah, instruksi 4000, deskripsi 1000, tanpa plan
kosong, bukan untuk task final), `PlanningStrategy` + allowlist
(`template.basic`, empat langkah deterministik), `Planner.plan`
(TaskEngine.get read-only, ProjectRegistry), `PlanningError` (9 kode),
prefix ID `pln_`, CLI `siamang planner [--json]` (read-only), cek `Planner`
di `validate`. 1929 test (1681 sebelumnya + 248 baru), offline, 0 skip.
Tanpa perubahan schema (v3), kontrak (v0.2), config, atau event.

Tidak dibangun (sesuai brief): eksekusi plan, System Analyst, Programmer,
QA, Reviewer, approval, loop agent, inspeksi project/filesystem/Git,
planner berbasis model, persistensi dan event plan, API, Telegram.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 14 System Analyst | Analisis yang sadar project (lewat tool read-only) untuk mengisi langkah "Identify affected components" |
| 14–18 | Eksekusi plan: memetakan `StepRole` ke agent, menjalankan langkah lewat `Orchestrator.execute` sesuai `execution_order()`, menerapkan hasil ke task lewat Task Engine |
| — | Strategi planning berbasis model lewat `PlanningStrategy` + Model Router, dengan validasi yang sama |
| 20/23 | Persistensi dan event plan bila dibutuhkan |

## Phase 14: System Analyst (DONE) [lama: 12]

Detail: [SYSTEM_ANALYST.md](SYSTEM_ANALYST.md).

Dibangun: `AnalysisRequest` (kunci eksekusi dan pemilihan file ditolak di
kedalaman mana pun; Plan opsional sebagai data), `Finding` dan `Component`
(confidence observed/inferred/unknown, evidence relatif yang ter-redaksi),
`SystemAnalysis` (imutabel, terbatas, `content_hash` tanpa `analysis_id`),
`AnalysisPolicy` (file, ukuran, byte, entri, kedalaman, finding, komponen,
output), `ProjectInspector` (satu-satunya akses filesystem: root terdaftar,
path relatif, tanpa symlink, file rahasia tidak dibuka, ber-budget),
`GitState` lewat `GitWorkspaceManager` (read-only), strategi
`analysis.basic` (struktur, nama dependensi manifest, file rahasia per
nama, kecocokan nama dengan request, fakta Git), `SystemAnalyst.analyze`,
`AnalysisError` (10 kode), prefix ID `ana_`, CLI `siamang analyst
[--json]` (read-only), cek `Analyst` di `validate`. Perbaikan lapisan
bawah: `siamang.log.redact` kini linear (sebelumnya kuadratik). 2269 test
(1929 sebelumnya + 340 baru), offline, 0 skip. Tanpa perubahan schema
(v3), kontrak (v0.2), config, atau event; `dependencies = []`.

Tidak dibangun (sesuai brief): Programmer, QA, Reviewer, approval, loop
otonom, pembungkusan agent, eksekusi plan, perubahan kode, mutasi Git,
pembuatan workspace, API, Telegram, persistensi dan event analisis,
model eksternal.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 15 Programmer | Mengonsumsi Plan + SystemAnalysis; mutasi file/Git hanya lewat tool mutating di belakang approval (Phase 19) |
| 15–18 | Analisis semantik (membaca kode sumber) lewat strategi baru di `KNOWN_STRATEGIES`, dengan budget inspector yang sama |
| — | Strategi berbasis model lewat `AnalysisStrategy` + Model Router, dengan validasi yang sama |
| — | Membungkus System Analyst sebagai Agent bila arsitektur membutuhkannya |
| 20/23 | Persistensi dan event analisis bila dibutuhkan |

## Phase 15: Programmer (DONE) [lama: 13]

Detail: [PROGRAMMER.md](PROGRAMMER.md).

Dibangun: `ProgrammingRequest` (Plan dan SystemAnalysis wajib sebagai
snapshot data; kunci eksekusi, mutasi, dan approval ditolak di kedalaman
mana pun), `ProposedChange` (create/modify/delete satu file teks relatif;
before-state absent/observed/unverified, `before_hash` hanya bila teramati,
`after_hash` dihitung; path tidak aman, file terlarang, rahasia, konten
biner/terlalu besar ditolak), `Patch` (satu operasi per path, konflik
huruf besar/kecil dan file/direktori ditolak, terbatas, `content_hash` tanpa
`patch_id`), `ProgrammingResult` (completed/waiting/failed dengan invarian,
imutabel sampai ke dalam), `ProgrammingPolicy` (batas keras, tanpa
override), `ProgrammerModelAccess` (hanya lewat ModelRouter, tanpa pin
model, sekali pakai), `ProjectSnapshot` (before-state dan file konteks lewat
`ProjectInspector` Phase 14), konteks model yang terbatas dan disaring
(tanpa `root_path`, path mesin, atau rahasia), strategi `programming.model`
dengan parsing output yang ketat, `Programmer.program`, `ProgrammingError`
(16 kode), prefix ID `prg_`/`pch_`, CLI `siamang programmer [--json]`
(read-only), cek `Programmer` di `validate`. 2757 test (2269 sebelumnya +
488 baru), offline, 0 skip. Tanpa perubahan schema (v3), kontrak (v0.2),
config, tool, atau event; `dependencies = []`.

Tidak dibangun (sesuai brief): mutasi workspace, penerapan patch, mutasi
Git, QA, Reviewer, Approval System, retry/loop otonom, loop QA ↔
Programmer, agent Programmer, rantai Orchestrator, lifecycle task otomatis,
persistensi/event, API, WebSocket, Telegram, adapter model
vendor.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 16 QA | Menguji perubahan yang *sudah diterapkan*; butuh lapisan mutasi terkontrol lebih dulu |
| 18 | Loop QA → Programmer → QA milik orkestrasi; Programmer sendiri tetap tanpa retry |
| 19 Approval | Memutuskan apakah `Patch` boleh diterapkan; memeriksa `before_hash` terhadap isi file saat itu |
| — | Lapisan mutasi workspace terkontrol (tool mutating di belakang approval) |
| — | Membungkus Programmer sebagai Agent dan rantai Planner → Analyst → Programmer di Orchestrator |
| — | Adapter model nyata (lokal/vendor) di `siamang.models.providers`, lewat ModelRouter |
| 20/23 | Persistensi riwayat patch dan event bila dibutuhkan |

## Phase 16: QA (DONE) [lama: 14]

Detail: [QA.md](QA.md).

Dibangun: `QARequest` (Plan, SystemAnalysis, dan Patch persis sebagai
snapshot data, plus `patch_hash` tercatat; kunci eksekusi, perbaikan, retry,
review, dan approval ditolak di kedalaman mana pun), `QAFinding` (kode,
severity error/warning/info, kategori patch/security/structure/consistency/
scope/policy, path aman atau kosong, pesan tanpa rahasia/path mesin),
`QAResult` (pass/fail/inconclusive diturunkan dari finding dan dicek,
`not_verified`, imutabel sampai ke dalam, `content_hash` tanpa `qa_id`/
`patch_id`), `QAPolicy` (batas keras), pemeriksaan deterministik (hash patch
tercatat, validasi ulang Phase 15 + deteksi manipulasi, konflik, batas
policy, langkah plan, kecocokan analisis/plan, before-state terhadap snapshot
read-only, symlink, bentrok huruf besar/kecil, scope), `QAService.verify`,
`QAError` (11 kode), prefix ID `qa_`, CLI `siamang qa [--json]` (read-only),
cek `QA` di `validate`. 3027 test (2757 sebelumnya + 270 baru), offline,
0 skip. Tanpa model, eksekusi, perubahan schema (v3), kontrak (v0.2),
config, tool, atau event; `dependencies = []`.

Tidak dibangun (sesuai brief): Reviewer/ReviewResult, retry/perbaikan/loop
QA ↔ Programmer, pemanggilan Programmer, Approval, penerapan patch,
eksekusi runtime/sandbox, agent QA, rantai Orchestrator, persistensi/event,
API, WebSocket.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 17 Reviewer | Review tingkat tinggi di atas `QAResult`; QA berhenti di `QAResult` |
| 18 | Loop QA → Programmer → QA milik orkestrasi; QA sendiri hanya melaporkan `fail` |
| 19 Approval | Memutuskan penerapan; `pass` bukan approval; cek `before_hash` sendiri saat menerapkan |
| — | Verifikasi runtime (menjalankan test) butuh keputusan arsitektur dan sandbox tersendiri |
| 20/23 | Persistensi hasil QA dan event bila dibutuhkan |

## Phase 17: Reviewer (DONE) [lama: 15]

Detail: [REVIEWER.md](REVIEWER.md).

Dibangun: `ReviewRequest` (Plan, SystemAnalysis, Patch, dan QAResult persis
sebagai snapshot data, plus `patch_hash` dan `qa_hash` tercatat; kunci
eksekusi, perbaikan, retry, approval, merge, deploy, dan verdict ditolak di
kedalaman mana pun), `ReviewFinding` (kode, severity error/warning/info,
kategori verification/alignment/scope/maintainability/consistency/risk/
security/feasibility, path aman atau kosong, pesan tanpa rahasia/path
mesin), `ReviewResult` (pass/concern/fail/inconclusive diturunkan dari
finding dan dicek terhadap status QA, `not_verified` wajib, imutabel sampai
ke dalam, `content_hash` tanpa `review_id`/ID artefak acak),
`ReviewerPolicy` (batas keras, tidak pernah approve), validasi ulang dan
deteksi manipulasi semua artefak (hash patch dan QA dihitung ulang, QAResult
terikat ke task/patch/plan/analisis), penilaian deterministik (QA sebagai
bukti, alignment berbasis nama dan lokasi analisis, scope, maintainability,
konsistensi nama/tipe file, risiko, feasibility), `ReviewerService.review`,
`ReviewerError` (11 kode), prefix ID `rev_`, CLI `siamang reviewer [--json]`
(read-only), cek `Reviewer` di `validate`. 3357 test (3027 sebelumnya + 330
baru), offline, 0 skip; 18 mutasi batas keamanan semuanya tertangkap. Tanpa
model, pembacaan file project, eksekusi, perubahan schema (v3), kontrak
(v0.2), config, tool, atau event; `dependencies = []`.

Tidak dibangun (sesuai brief): approval/approve/reject, penerapan patch,
commit/merge, retry/perbaikan/regenerasi/loop QA ↔ Programmer, pemanggilan
Programmer atau QA, reviewer berbasis model, agent Reviewer, rantai
Orchestrator, persistensi/event, API, WebSocket.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 18 | Loop QA → Programmer → QA (dan mungkin Reviewer) milik orkestrasi; Reviewer sendiri hanya melaporkan `fail`/`concern` |
| 19 Approval | Memutuskan penerapan; review `pass` bukan approval; provenance artefak (siapa yang membuat patch/QA/review) dan cek `before_hash` sendiri saat menerapkan |
| — | Review berbasis diff terhadap isi file saat ini butuh keputusan akses baca terkontrol |
| 20/23 | Persistensi hasil review dan event bila dibutuhkan |

## Phase 18: QA → Programmer → QA Loop (DONE) [lama: 16]

Detail: [LOOP.md](LOOP.md).

Dibangun: `LoopRequest` (ID task, instruksi, Plan dan SystemAnalysis persis,
input/constraint/metadata pemanggil; kunci Reviewer/QA/Programmer plus
`loop_feedback`/`max_iterations`/`budget`/`unlimited` ditolak di kedalaman
mana pun), `LoopPolicy` (3 putaran, batas keras 5; anggaran panggilan
Programmer/QA/Reviewer; batas jumlah dan byte feedback; retry pada
`concern`/`fail`; `pass` tidak pernah diulang; tanpa nilai tak terbatas atau
override), `LoopFeedback` (hanya kode/severity/pesan/path aman dari finding
QA dan Reviewer; teks berbahaya ditahan; terurut, tanpa duplikat, terbatas),
`LoopIteration` (catatan imutabel per putaran), `LoopResult` (status = bukti
terakhir, `stop_reason` terpisah, `pass` hanya bila `success`, artefak akhir
terikat ke putaran terakhir yang dievaluasi, jumlah panggilan cocok),
`LoopService.run` (state machine berhingga; feedback lewat
`ProgrammingRequest.input` tanpa mengubah Programmer; no-progress lewat
content hash patch; setiap artefak divalidasi ulang dan diikat ke
putarannya), `LoopError` (6 kode), prefix ID `lop_`, CLI `siamang loop
[--json]` (read-only), cek `Loop` di `validate`. 3602 test (3357 sebelumnya +
245 baru), offline, 0 skip; 21 mutasi batas semuanya tertangkap. Tanpa
penerapan patch, approval, eksekusi, mutasi Git, pemilihan model, jaringan,
perubahan schema (v3), kontrak (v0.2), config, tool, atau event;
`dependencies = []`.

Tidak dibangun (sesuai brief): approval/approve/reject/token, penerapan
patch, commit/merge/deploy, eksekusi runtime, agent Loop, rantai
Orchestrator, persistensi/event, API, WebSocket.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 19 Approval | Memutuskan apakah `final_patch` (usulan) boleh diterapkan; provenance artefak (komponen yang di-inject dipercaya Loop); cek `before_hash` sendiri saat menerapkan |
| — | Lapisan mutasi workspace terkontrol di belakang approval |
| — | Merangkai Planner → Analyst → Loop di Orchestrator bila dibutuhkan |
| 20/23 | Persistensi hasil loop dan event bila dibutuhkan |

## Phase 19: Approval System (DONE) [lama: 17]

Detail: [APPROVAL.md](APPROVAL.md).

Dibangun: `ApprovalDecision` (approve/reject eksplisit tanpa default;
`approver_id` polos; alasan opsional untuk approve, wajib untuk reject,
ditolak bila tidak aman), `ApprovalPolicy` (set approver eksplisit, kosong =
tidak ada, tanpa wildcard/aktor ajaib/override/kedaluwarsa; batas alasan
300 dari batas keras 600), `ApprovalProvenance` (ID + content hash task,
project, plan, analisis, loop, putaran akhir, patch, QA, review),
`ApprovalRequest` (artefak akhir dari LoopResult; provenance dicatat saat
dibuat; tanpa field approver/role/otorisasi), `ApprovalResult`
(approved/rejected, bukti, versi policy, persyaratan; approval atas bukti
yang tidak lolos tidak bisa dibuat; `approval_hash`; seal HMAC-SHA256),
`ApprovalService.decide`/`verify` (otoritas dari policy, task terbuka dan
project aktif, provenance dihitung ulang, penilaian Reviewer dan cek statis
QA diturunkan ulang, approve hanya bila QA/review/loop pass dan loop
`success`), `ApprovalError` (9 kode), CLI `siamang approval [--json]`
(read-only), cek `Approval` di `validate`. Refactor kecil: urutan penilaian
Reviewer diekstrak sebagai `reviewer.checks.judge` (perilaku sama). 3763
test (3602 sebelumnya + 161 baru), offline, 0 skip; 25 mutasi batas
semuanya tertangkap. Tanpa penerapan patch, eksekusi, mutasi Git, model,
jaringan, persistensi, event, perubahan schema (v3) atau kontrak (v0.2);
`dependencies = []`.

Tidak dibangun (sesuai brief): autentikasi/login/OAuth/JWT/RBAC, tanda
tangan digital/PKI, kedaluwarsa berbasis waktu, override/force/emergency,
perintah CLI approve, penerapan patch, commit/merge/deploy, event
`approval.*`, persistensi, API, WebSocket, UI.

Diserahkan ke phase berikutnya:

| Phase | Hal terbuka |
|---|---|
| 20 API | Autentikasi approver (sekarang `approver_id` = konteks pemanggil) dan penyampaian keputusan lewat API |
| Integrasi klien | Kanal approval interaktif yang menghasilkan `ApprovalDecision` terautentikasi |
| 23 Event | Emisi `approval.required/granted/rejected` (kontrak v0.2) dan persistensi hasil |
| — | Lapisan eksekusi: menerapkan patch yang disetujui dengan cek `before_hash` sendiri terhadap file saat itu; tanda tangan bila hasil approval harus melintasi proses |

## Phase 19.5: Integrasi Penuh & Uji Adversarial (PASS WITH FINDINGS)

Gerbang validasi, bukan fitur: 0 perubahan source produksi. 4139 test
(3763 sebelumnya + 376 baru di `tests/integration/`), offline, 0 skip.
48 mutasi keamanan pada `approval/` semuanya tertangkap. Temuan: M-1
(metadata Phase 10–14 hanya dibekukan satu tingkat; tertahan oleh content
hash), L-1, L-2, I-1–I-3. M-1 dan L-1 diselesaikan di Phase 20.

Masih terbuka:

* **L-2:** `import siamang.approval` ikut memuat modul lain lewat re-export di `__init__`.
* **I-1:** usulan yang hanya mengubah dokumen bisa memicu `scope.outside_analysis` dan `concern`.
* **I-3:** ada siklus modul di `models`/`config`/`log`, di luar rantai approval.

## Catatan urutan (untuk diputuskan, tidak mengubah tabel di atas)

| Isu | Detail | Usulan |
|---|---|---|
| Event System di Phase 20 | Envelope, `seq`, dan replay dibutuhkan sejak Phase 3 (kontrak) dan Phase 8 (task engine) | Phase 3 membangun event *internal* minimal. Phase 20 dibaca sebagai event system *lanjutan* (persisten/terdistribusi/eksternal) |
| Approval System di Phase 17 | Tool berisiko ada sejak Phase 7, dan Programmer menulis file sejak Phase 13 | Phase 7 sudah memblokir aksi `medium`/`high` secara default (deny-by-default). Phase 17 membangun kanal approval interaktif. **Phase 15 (baru):** Programmer hanya *mengusulkan* patch dan tidak menulis file; penerapan menunggu Approval (Phase 19 baru) |
| Benchmark model | Model 4B lokal belum terbukti untuk coding | Phase 4 menyertakan benchmark model per peran sebelum Phase 11–15 |


## Phase 20 HTTP API and truthful proposal runtime

The user authorized backend work before Virtual Office VO-02. Implemented:
optional FastAPI transport, scoped bearer authentication, strict/idempotent task
submission, explicit single-worker proposal scheduling, persistent execution
phase/heartbeat, interrupted recovery without automatic re-execution, sealed
artifact reports, and explicit/idempotent approval via the existing service.
Core lifecycle v0.2/schema v3 stay intact; API supplemental schema is v1.
Metadata deep-freeze and the public ApprovalService policy reference are hardened.
Ollama generation is available through the existing model layer; echo is refused
by the HTTP engineering runtime. No patch apply/execution, WebSocket, SSE,
Virtual Office frontend, commit, push, or deployment was added.

See HTTP_API.md.

## Phase 21 Agent documents, portfolio, and design gate

Implemented: `claude_cli` provider, per-agent models in `siamang.toml`, a catalog of every
agent's outputs with `authored`/`derived`/`measured` sources, the design gate before coding,
features, repository link, and portfolio endpoints (API schema v2, additive). See HTTP_API.md.

Also implemented: GitHub projects from the browser, the Lead's questions and an approved project
spec, one `siamang/*` branch per task, revisions, and commit plus pull request after approval. This
deliberately ends "patches are never applied", behind approval and never on a default branch.

The Lead's conversation before a task exists, resumable stops, and runtime-selected Programmer files are in API schema v3.

Several Programmers per task, empty repositories, and CI results as `measured` documents are in API schema v4.

Next: feeding model review findings into the revision loop, and tasks of different projects at the same time.
Today one worker runs one task at a time, however many packages that task has.

## Phase 27 Tool Executor dan Phase 28 Recovery Engine

Keduanya mati secara default dan menyala lewat konfigurasi operator (lihat `siamang.example.toml`). Dengan keduanya mati,
perilaku run sama seperti sebelumnya.

**Tool Executor** (`siamang.executor`, `[sandbox] tools = true`, perlu `sandbox.enabled`): loop giliran terbatas
(`tool_turns`, `tool_runs`, dan batas panggilan salah). Agen memanggil satu tool per balasan JSON: `list_files`, `read_file`,
`write_file`, `delete_file`, `run`, `finish`. Edit hanya ada di overlay memori dan proyek tidak pernah ditulis. Setiap `run`
berjalan lewat `SandboxService` (tanpa jaringan dan tanpa secret) dan melihat semua edit sejauh ini. File mirip secret,
symlink, dan isi yang tampak seperti kredensial ditolak di kedua arah. `verified` hanya benar bila perintah terakhir keluar
dengan kode 0 setelah edit terakhir.

Sambungan ke Programmer: strategi `programming.tools` (daftar putih berdasarkan ID, seperti `programming.model`). Hasil
agen menjadi draft yang sama dengan balasan satu tembakan, lalu divalidasi ulang oleh Programmer (path, step, ukuran,
before-state, hash). Agen tidak pernah menetapkan before-state. Akses model dibatasi `tool_turns + 1` generasi per percobaan,
tidak lagi satu. Tanpa mesin container, strategi jatuh ke satu tembakan dan menulis peringatan. Hasil tanpa lari lulus setelah
edit terakhir diberi peringatan "not verified" dan menjadi utang teknis. Agen boleh berhenti dengan `questions`, yang menjadi
hasil `waiting` seperti biasa. Metadata hasil memuat `sandbox_*` tanpa perintah (aturan lama melarang perintah di hasil).

**Recovery Engine** (`siamang.recovery`, `[recovery] enabled = true`): menggantikan loop verifikasi-ulang pada run satu
Programmer. Satu tahap `deliver` (loop Programmer, QA, Reviewer, lalu verifikasi di sandbox). Tingkat 1 mengulangnya dengan
alasan gagal sebagai `recovery_feedback` dan keluaran pemeriksaan sebagai `verification_failure`. Tingkat 2 meminta model Lead
(atau Analyst) memilih `change_approach`, `split`, atau `give_up`; jawaban tak terpakai berarti `give_up`. Tingkat 3 menyusun
ulang rencana dari instruksi ditambah alasan gagal, lalu mengulang delivery. Semua dibatasi (`retries`, `advisor_rounds`,
`replans`, `max_attempts`). Bila semua gagal, run berhenti di `quality_gate` dengan `report.recovery` yang memuat tahap dan alasannya.

Checkpoint: hasil delivery yang selesai disimpan di `data/checkpoints/` dengan kunci task + sidik jari brief, jawaban pemilik,
dan desain, lalu dihapus setelah laporan tersimpan. Bila run mati di antaranya, percobaan berikutnya dengan masukan sama tidak
mengulang Programmer, QA, Reviewer, dan pemeriksaan. Checkpoint lebih tua dari 6 jam diabaikan. Kompromi (saran penasihat,
rencana yang disusun ulang, tes yang tak dijalankan Programmer) masuk `report.recovery.debt` dan bagian "Technical debt" di
deskripsi PR.

Batas yang jujur: belum diuji dengan container nyata (hanya mesin tiruan); run paket paralel memakai jalur lama untuk
recovery tetapi Programmer-nya sudah bisa memakai tool; replan memakai Planner berbasis templat, jadi langkahnya bisa sama
dan hanya instruksinya yang berubah; checkpoint hanya untuk tahap delivery.

## Pemecahan pekerjaan besar

Bagian dari Phase 28. Pemicunya task nyata: upgrade framework pada repo berisi 3138 file gagal berulang di tahap Arsitek,
karena satu panggilan model diminta menulis 8 dokumen sekaligus dan selalu timeout.

**Tahap desain:** dokumen ditulis maksimal 3 per panggilan, 3 panggilan bersamaan. Setiap panggilan tercatat di
`api_design_parts` dengan waktu nyatanya, dan dokumennya disimpan begitu panggilan itu selesai. Hanya bagian yang gagal
diulang, sampai 3 putaran (300, 450, 600 detik). Konteks dibatasi: 400 nama file dengan setiap folder terwakili, dan 4.000
karakter per dokumen sebelumnya.

**Melanjutkan:** retry memakai ulang dokumen yang sudah `generated` dan pembagian kerjanya, selama instruksi, spesifikasi,
percakapan, dan identitas proyek sama (`api_document_basis`). Run yang dibuat sebelum tabel ini ada tidak bisa dipakai ulang.

**Paket kerja:** gelombang menggantikan "foundation lalu semua paralel". Batas paket mengikuti ukuran repo (4 sampai 10), dengan
maksimal 4 gelombang. Paket satu gelombang tidak boleh berbagi path; gelombang berikutnya boleh, dan melihat hasil
sebelumnya. Setiap paket menerima kontrak bersama, daftar paket lain, dan ringkasan gelombang sebelumnya. Paket yang timeout
dicoba sekali lagi, lalu dibelah dua.

**Kejujuran pembagian:** jawaban yang ditolak kode atau kegagalan model ditanyakan sekali lagi dengan alasannya. Bila tetap
gagal, dokumen berstatus `failed`. `not_applicable` hanya untuk penilaian Analyst sendiri, beserta alasannya.
`POST /v0/tasks/{id}/work-division` mengulang pembagian saja di gerbang desain.

Batas yang jujur:

- Tahap desain sudah berjalan dengan model nyata, tetapi gelombang, pembelahan paket, dan bagi ulang baru diuji dengan model
  tiruan.
- Dokumen dalam satu tahap ditulis tanpa saling membaca, jadi konsistensinya bergantung pada dokumen tahap sebelumnya.
- Percobaan ulang tahap desain hanya menambah waktu. Prinsip "kegagalan mengubah strategi" di [VISION.md](VISION.md) belum
  terpenuhi di sini.
- Paket satu gelombang hanya terhubung lewat kontrak. Kontrak yang kurang spesifik baru terlihat di putaran integrasi, saat
  semua paket sudah selesai.
- Belahan paket memiliki file yang sudah ada. File baru yang langsung berada di folder induknya akan ditolak.
- Retry memulai ulang semua paket dari `pending`, termasuk yang sudah lulus di run sebelumnya, dan desain perlu disetujui lagi.
- `execution-events` mengembalikan event terlama lebih dulu. Klien harus memakai `after` agar task yang sering diulang tetap
  terbaca sampai event terbaru. Virtual Office belum melakukannya.
