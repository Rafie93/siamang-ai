# Contract v0.1 examples

The 10 JSON examples from `docs/CONTRACT.md` (Phase 2), as
complete messages. They are the canonical fixtures: `siamang validate` and
the test suite both load them from here.

| File | Source in contract doc | Changes vs. the doc |
|---|---|---|
| `01_envelope.json` | §3 Envelope | Doc payload is a `{ }` placeholder; `task.progress` requires `summary`, so a minimal one was added |
| `02_task_request.json` | §4.1 `task.request` | None (verbatim) |
| `03_task_accepted.json` | §4.1 `task.accepted` | Envelope completed (doc example is abbreviated) |
| `04_task_started.json` | §4.2 `task.started` | Envelope completed |
| `05_task_progress.json` | §4.2 `task.progress` | Envelope completed |
| `06_task_waiting_approval.json` | §4.2 `task.waiting_approval` | Envelope completed |
| `07_approval_required.json` | §4.3 `approval.required` | Envelope completed |
| `08_approval_granted.json` | §4.3 `approval.granted` | Envelope completed (no `seq`: inbound message) |
| `09_task_completed.json` | §4.4 `task.completed` | Envelope completed |
| `10_task_failed.json` | §4.5 `task.failed` | Envelope completed |

Payloads are copied verbatim. Envelope values that the doc omitted
(`event_id`, `timestamp`, `request_id`, ...) were filled in consistently with
the §4.1 request (`req_01J9Z8P2AA` / `tsk_01J9Z8P5BB`).

Each file is a standalone example, not one ordered stream. Note that the doc
gives both `approval.required` and `task.waiting_approval` `seq: 6`; in a real
stream they would be 6 and 7 (see docs/PHASE_ROADMAP.md, Phase 3 findings).
