# Kontrak aktual HTTP

Sumber: backend Siamang, `src/siamang/api` (`app.py`, `runtime.py`, `store.py`, `settings.py`). Kontrak OpenAPI yang sedang berjalan ada di `GET /v0/openapi.json`.

| Method | Route | Permission / behavior |
|---|---|---|
| GET | `/v0/health` | read; storage health/version and observation timestamp |
| GET | `/v0/capabilities` | read; truthful model configuration, permissions, supported statuses; `execution` = `{sandbox, tools, recovery}` says what this backend will actually do; `execution_phases` now includes `verifying` and `recovering` |
| GET | `/v0/openapi.json` | read; generated OpenAPI contract, bearer security scheme |
| GET | `/v0/stream?after=0` | read; Server-Sent Events. Detail di [HTTP_API.md](HTTP_API.md#event-stream) |
| GET | `/v0/projects` | read; scoped registered projects, local root paths withheld |
| GET | `/v0/projects/{project_id}` | read; scoped project |
| GET | `/v0/tasks?project_id=...&status=...&limit=100&offset=0` | read; lifecycle + execution snapshots |
| POST | `/v0/tasks` | submit; persist a task, no automatic execution |
| GET | `/v0/tasks/{task_id}` | read; task and latest execution record |
| GET | `/v0/tasks/{task_id}/events?after_seq=0&limit=100` | read; lifecycle envelope replay |
| GET | `/v0/tasks/{task_id}/execution-events?after=0&limit=100` | read; persisted real stage transitions |
| POST | `/v0/tasks/{task_id}/run` | run; schedule queued task, 202 after storage succeeds |
| POST | `/v0/tasks/{task_id}/cancel` | cancel; explicit cooperative cancellation |
| POST | `/v0/tasks/{task_id}/retry` | retry; counted requeue only, no execution |
| POST | `/v0/tasks/{task_id}/work-division` | run; 202, hanya pembagian kerja yang diulang; 409 `design_not_pending` di luar gerbang desain |
| GET | `/v0/tasks/{task_id}/reports` | read; plan, analysis, loop, patch, static QA, review, plus (API schema 5, each `null` when the run did not record it) `verification` (sandbox checks and criteria coverage), `recovery` (tiers, attempts, debt), `programmer_sandbox` (turns, runs, verified; no commands) |
| GET | `/v0/approvals?project_id=...` | read; waiting proposals with complete evidence and eligibility; `verification` and `recovery` are status words (or `null`) |
| POST | `/v0/approvals/{run_id}` | approve; explicit approve/reject through ApprovalService |


## Payload dan response

Submit JSON wajib project_id, description (1–4000 karakter), request_id; opsional title dan priority low/normal/high/urgent (default normal). Extra field ditolak. Source ditetapkan server. Respons 202 `{task, duplicate, scheduled:false, observed_at}`. Reuse request_id untuk pengulangan submission yang sama; jangan membuat ID baru otomatis ketika outcome timeout belum diketahui. Konflik input tidak dianggap submission baru.

Run tanpa payload menghasilkan 202 `{run_id, task_id, state:"scheduled"}`. Ini penerimaan jadwal, bukan hasil kerja. Cancel mengembalikan task; retry `{task, scheduled:false}`. Tidak ada retry POST run secara buta.

GET projects/list tasks: `{items, observed_at}`. Project detail object langsung. Task detail object task ditambah execution dan observed_at. Task berisi task_id/project_id/request_id, description, lifecycle dan metadata domain (lihat fixture); jangan mengandalkan semua response memiliki envelope seragam.

Events: `{items, next_after_seq}` memakai seq per-task; execution-events: `{items, next_after}` memakai position. Cursor berbeda, jangan digabung. Limit 1–100. Tasks memerlukan project_id; status opsional lifecycle, offset >=0. Approvals memerlukan project_id, maksimum 100 dan tidak punya pagination.

Reports: `{items:[{run_id,plan,analysis,loop,patch,qa,review}],patch_applied:false,sanitized:true}`. Field artefak bisa null/absen; jangan menggantinya dengan hasil sintetis. Approvals: `{items:[{run_id,task_id,eligible,wait_reason,provenance}]}`; eligible bukan pengganti permission dan validasi backend.

Decision JSON `{decision:"approve"|"reject",reason?:string}`; reason <=300 karakter, reject wajib alasan. Response objek hasil ApprovalService, bukan task. Tidak menerima identity/permission/seal/patch/QA dari browser. Keputusan identik oleh identitas sama idempotent; konflik 409. Approval berhasil -> completed/finished dengan patch_applied=false; reject -> cancelled.

Semua route perlu Authorization Bearer dan permission terkait. Scope project ditentukan server. Error domain umumnya `{error:{code}}`: 401 unauthorized, 403 forbidden, 404 tidak ditemukan/di luar scope baca, 409 konflik state/evidence/busy, 413 body >64KiB, 422 invalid_request/domain validation, 503 model_not_configured/runtime_unavailable, 500 internal_error. Adapter harus menangani pula non-JSON/network error. Respons no-store. Docs UI otomatis dinonaktifkan.

## Pemetaan status untuk frontend

Lifecycle: queued/running/waiting/completed/failed/cancelled. Execution phase terpisah: queued/planning/analysis/programming/qa/reviewing/awaiting_approval/blocked/interrupted/finished/failed/cancelled.

Aktivitas tahap hanya divisualisasikan saat snapshot baru, lifecycle running, execution.activity_known=true dan stale=false. Heartbeat setiap 3 detik, stale setelah 15 detik menurut server. Tidak ada runtime record: phase=null, state=unknown, activity_known=false. recovery_required dapat menandai legacy running. Status tak dikenal -> netral, bukan sukses.

Waiting perlu wait_reason: approval_required, quality_gate, input_required, worker_interrupted. Jangan menganggap setiap waiting menunggu approval. Finished/completed berarti proposal disetujui; bukan patch diterapkan atau tes runtime kode lulus.

Ikuti `/v0/stream` dan muat ulang yang disebutnya. Bila stream tidak tersedia, polling setiap 3 detik dengan backoff saat gagal. Hentikan keduanya ketika view dilepas, tampilkan label stale dan waktu observasi. Pisahkan connection state dari task state, jangan membuat persen progress. Klien yang tidak lagi polling wajib memakai event `heartbeat` untuk `heartbeat_at`, karena kesegaran dinilai dari nilai itu. Polling cepat tetap dapat melewatkan tahap singkat; replay execution-events memberi histori nyata.

## Dokumen, proyek, dan gate desain

Rute tambahan yang dibuka gateway: `GET /v0/portfolio`, `GET|POST /v0/projects/{id}/features`, `POST /v0/projects`, `POST /v0/projects/{id}/clone`, `GET /v0/projects/{id}/intake`, `POST /v0/projects/{id}/intake/messages`, `POST /v0/projects/{id}/intake/approve`, `GET|POST /v0/tasks/{id}/messages`, `POST /v0/tasks/{id}/publish`, `GET /v0/tasks/{id}/documents`, dan `POST /v0/tasks/{id}/design-decision`. Detail ada di `../siamang/docs/HTTP_API.md`. Fase baru: `clarification`, `briefing`, `design_approval`, `reporting`. `wait_reason` baru: `clarification_required`, `design_approval`.
