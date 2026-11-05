# Siamang Contract

**Contract version:** `0.2` (DRAFT). Diimplementasikan sebagai kode di
`src/siamang/contracts/`.

Kontrak ini mendefinisikan pesan antara Siamang dan klien (CLI, antarmuka web,
atau layanan lain). Klien hanya mengenal: **REQUEST, STATUS, PROGRESS, APPROVAL,
RESULT, ERROR.** Planner, agent, model, dan database Siamang berada di belakang
kontrak dan boleh berubah tanpa memengaruhi klien.

> **Yang berjalan hari ini:** envelope dan event lifecycle di dokumen ini dipakai
> untuk penyimpanan dan replay event task. Route HTTP, body request, tahap
> eksekusi, dan approval yang sebenarnya ada di [HTTP_API.md](HTTP_API.md), yang
> didahulukan bila ada perbedaan.

---

## 1. Prinsip

| Prinsip | Konsekuensi |
|---|---|
| Klien mengirim tujuan, bukan rencana | `task.request` membawa `instruction` utuh dan `source`. Perencanaan milik Siamang |
| Pesan harus bisa dikorelasikan dan diserialisasi | Envelope JSON wajib punya `request_id`, `task_id`, `event_id`, `seq` |
| Aksi berisiko tidak dilewati diam-diam | Ada `approval.required` dan jawaban eksplisit; kedaluwarsa berarti ditolak |
| Keluaran bisa ditampilkan apa adanya | Setiap event untuk pengguna membawa `summary` berupa teks pendek |
| Project dimiliki Siamang | Klien mengirim `project.hint`; Siamang me-resolve dan mengembalikan `project.id` |

---

## 2. Transport

| Opsi | Status |
|---|---|
| HTTP untuk command dan untuk membaca state | **Berjalan.** Lihat [HTTP_API.md](HTTP_API.md) |
| SSE untuk event push (`GET /v0/stream`) | **Berjalan.** Envelope dikirim utuh sebagai event `lifecycle`. Lihat [Event stream](HTTP_API.md#event-stream) |
| WebSocket | Tidak dipakai. Perintah sudah lewat HTTP, jadi tidak ada lalu lintas dua arah yang perlu dilayani |

---

## 3. Envelope (semua pesan)

```json
{
  "contract_version": "0.2",
  "type": "task.progress",
  "event_id": "evt_01J9Z8Q7K3",
  "request_id": "req_01J9Z8P2AA",
  "task_id": "tsk_01J9Z8P5BB",
  "seq": 4,
  "timestamp": "2026-10-04T10:15:02Z",
  "producer": "siamang",
  "payload": { }
}
```

| Field | Wajib | Keterangan |
|---|---|---|
| `contract_version` | ✓ | `MAJOR.MINOR` |
| `type` | ✓ | Nama event (§4) |
| `event_id` | ✓ | Unik per pesan (ULID disarankan) |
| `request_id` | ✓ | Dibuat klien; kunci idempotensi |
| `task_id` | ✓ kecuali pada `task.request` | Dibuat Siamang |
| `seq` | ✓ untuk event Siamang | Naik monoton **per task**, untuk ordering dan replay |
| `timestamp` | ✓ | ISO-8601 UTC |
| `producer` | ✓ | `siamang` atau nama klien |
| `payload` | ✓ | Spesifik per `type` |

---

## 4. Events

Arah: **C→S** = klien ke Siamang, **S→C** = Siamang ke klien.

### 4.1 Request

#### `task.request` (C→S)
* **Purpose:** meminta Siamang mengerjakan tugas engineering.
* **Required:** `instruction`, `source`.
* **Optional:** `project.hint`, `project.id`, `locale`, `context.utterance`,
  `context.conversation`, `constraints.approval_policy`,
  `constraints.deadline`, `requested_by`.

```json
{
  "contract_version": "0.2",
  "type": "task.request",
  "event_id": "evt_01J9Z8P2A0",
  "request_id": "req_01J9Z8P2AA",
  "timestamp": "2026-10-04T10:15:00Z",
  "producer": "web-client",
  "payload": {
    "instruction": "Tambahkan fitur inventory dengan CRUD barang",
    "source": "api",
    "project": { "hint": "toko-contoh" },
    "locale": "id-ID",
    "requested_by": "owner",
    "constraints": { "approval_policy": "ask_medium_and_above" }
  }
}
```

Instruksi dikirim **utuh**; klien tidak memecahnya menjadi langkah.

#### `task.accepted` (S→C)
* **Purpose:** konfirmasi task dibuat (atau dikenali sebagai duplikat
  `request_id`).
* **Required:** `status: "queued"`, `summary`.
* **Optional:** `project.id`, `project.name`, `duplicate: true`.

```json
{ "type": "task.accepted", "task_id": "tsk_01J9Z8P5BB", "seq": 1,
  "payload": { "status": "queued", "project": { "id": "prj_example", "name": "toko-contoh" },
               "summary": "Tugas diterima untuk project toko-contoh." } }
```

*(Envelope dipersingkat pada contoh berikut. Semua field wajib §3 tetap
berlaku.)*

### 4.2 Progress

#### `task.started` (S→C)
* **Purpose:** eksekusi dimulai.
* **Required:** `summary`. **Optional:** `plan_outline` (≤ 7 judul langkah,
  plain text).

```json
{ "type": "task.started", "seq": 2,
  "payload": { "summary": "Mulai mengerjakan fitur inventory.",
               "plan_outline": ["Analisis struktur project", "Buat model Barang", "Buat endpoint CRUD", "Tulis test", "Review"] } }
```

#### `task.progress` (S→C)
* **Purpose:** kemajuan yang layak diketahui pengguna (bukan log internal).
* **Required:** `summary`. **Optional:** `percent` (0–100 atau null),
  `step` (`{index, total, title}`), `stage` (label bebas; **jangan** diandalkan
  klien untuk logika).

```json
{ "type": "task.progress", "seq": 5,
  "payload": { "summary": "Endpoint CRUD selesai, menulis test.", "percent": 70,
               "step": { "index": 4, "total": 5, "title": "Tulis test" } } }
```

Siamang wajib membatasi frekuensi (disarankan ≤ 1 per 5 detik per task).

#### `task.waiting_approval` (S→C)
* **Purpose:** transisi status. Task berhenti sampai approval dijawab.
* **Required:** `approval_id`, `summary`.

```json
{ "type": "task.waiting_approval", "seq": 6,
  "payload": { "approval_id": "apr_01J9Z9", "summary": "Menunggu persetujuan." } }
```

> `task.waiting_approval` = **status task**. `approval.required` = **detail
> permintaan**. Keduanya selalu dikirim berpasangan, dengan
> `approval.required` lebih dulu.

### 4.3 Approval

#### `approval.required` (S→C)
* **Purpose:** meminta keputusan manusia atas aksi berisiko.
* **Required:** `approval_id`, `action`, `risk`, `question`, `expires_at`.
* **Optional:** `details` (mis. `{files_changed, diff_stat, command}`),
  `allowed_channels`, `default_on_timeout` (selalu `"reject"`).

```json
{ "type": "approval.required", "seq": 6,
  "payload": {
    "approval_id": "apr_01J9Z9",
    "action": "apply_changes",
    "risk": "medium",
    "question": "Mengubah 4 file dan menambah 1 tabel database. Boleh dilanjutkan?",
    "details": { "files_changed": 4, "diff_stat": "+212 -8", "branch": "siamang/tsk_01J9Z8P5BB" },
    "allowed_channels": ["web"],
    "expires_at": "2026-10-04T10:25:00Z",
    "default_on_timeout": "reject"
  } }
```

`risk` ∈ `low | medium | high`.

#### `approval.granted` / `approval.rejected` (C→S)
* **Purpose:** jawaban manusia.
* **Required:** `approval_id`, `decided_by`, `channel`.
* **Optional:** `reason`.

```json
{ "type": "approval.granted", "task_id": "tsk_01J9Z8P5BB", "producer": "web-client",
  "payload": { "approval_id": "apr_01J9Z9", "decided_by": "owner", "channel": "web" } }
```

Aturan:

* Jawaban untuk `approval_id` yang sudah diputus atau kedaluwarsa ditolak
  dengan error `APPROVAL_NOT_PENDING`.
* Kedaluwarsa berarti dianggap `rejected`.
* Siamang memverifikasi `channel` ∈ `allowed_channels`.

### 4.4 Completion

#### `task.completed` (S→C)
* **Required:** `summary`, `outcome` (`success | partial`).
* **Optional:**
  * `result.changes` (`{files_changed, commits, branch}`)
  * `result.tests` (`{passed, failed}`)
  * `result.artifacts` (path/URL)
  * `result.next_steps` (plain text)
  * `duration_secs`

```json
{ "type": "task.completed", "seq": 11,
  "payload": { "outcome": "success",
               "summary": "Fitur inventory selesai. Perubahan ada di branch siamang/tsk_01J9Z8P5BB.",
               "result": { "changes": { "files_changed": 4, "commits": 2, "branch": "siamang/tsk_01J9Z8P5BB" } },
               "duration_secs": 412 } }
```

#### `task.cancel` (C→S) → `task.cancelled` (S→C)
* `task.cancel` required: `reason` (boleh kosong).
* `task.cancelled` required: `summary`. Optional: `rolled_back: bool`.

### 4.5 Failure

#### `task.failed` (S→C)
* **Required:** `summary`, `error.code`, `error.retryable`.
* **Optional:** `error.message` (teknis), `error.step`, `rolled_back`.

```json
{ "type": "task.failed", "seq": 9,
  "payload": { "summary": "QA menahan hasil setelah tiga kali perbaikan. Perubahan tidak diterapkan.",
               "error": { "code": "QA_FAILED", "retryable": true, "message": "scope finding on src/inventory.py" },
               "rolled_back": true } }
```

| `error.code` | Arti | retryable |
|---|---|---|
| `INVALID_REQUEST` | Payload tidak valid | false |
| `UNSUPPORTED_CONTRACT_VERSION` | MAJOR tidak didukung | false |
| `PROJECT_NOT_FOUND` / `PROJECT_AMBIGUOUS` | Hint tidak bisa di-resolve (ambiguous: `details.candidates`) | false |
| `APPROVAL_REJECTED` / `APPROVAL_TIMEOUT` | Manusia menolak / tidak menjawab | false |
| `APPROVAL_NOT_PENDING` | Jawaban approval terlambat/duplikat (respon HTTP, bukan event) | false |
| `TOOL_FAILED` | Eksekusi tool gagal | tergantung |
| `QA_FAILED` | Pemeriksaan atau review tidak lolos | true |
| `MODEL_UNAVAILABLE` | Model/provider tidak tersedia (**tidak ada fallback mock**) | true |
| `CANCELLED` | Dibatalkan | false |
| `INTERNAL` | Lainnya | false |

### 4.6 Lifecycle task (v0.2)

MINOR aditif (§7): consumer v0.1 cukup mengabaikan type dan field yang tidak
dikenal. Semua event di bawah diproduksi Siamang dan memakai `seq` per task.

* **Field opsional baru** pada `task.started`, `task.completed`,
  `task.cancelled`, `task.failed`: `status` (status task sesudah event) dan
  `project {id, name?}`. `task.completed` juga boleh membawa `details`
  (objek, metadata eksekusi).
* `task.waiting`. Required: `summary`. Optional: `status`, `project`, `reason`.
* `task.requeued`. Required: `summary`. Optional: `status`, `project`.
* `task.retry_requested`. Required: `summary`, `retry_count` (≥ 1),
  `max_retries` (≥ 1). Optional: `status`, `project`.

```json
{ "type": "task.retry_requested", "seq": 4,
  "payload": { "summary": "Retry 1/3: Tambahkan fitur inventory", "status": "queued",
               "project": { "id": "prj_example" }, "retry_count": 1, "max_retries": 3 } }
```

`task.waiting` adalah status umum (mis. menunggu input); `task.waiting_approval`
tetap ada untuk approval.

---

## 5. Siklus status task

Status tersimpan: `queued`, `running`, `waiting`, `completed`, `failed`,
`cancelled`. `waiting → queued` (requeue) dan `failed → queued` (retry eksplisit,
dihitung) diizinkan. Detail: [TASK_ENGINE.md §3](TASK_ENGINE.md#3-lifecycle).

---

## 6. Aturan keamanan kontrak

1. Siamang **tidak** menjalankan aksi `medium`/`high` tanpa `approval.granted`,
   kecuali `constraints.approval_policy` mengizinkan **dan** kebijakan lokal
   Siamang juga mengizinkan. Kebijakan Siamang selalu menang.
2. Klien tidak boleh mengirim approval tanpa input manusia yang nyata.
3. Kontrak tidak pernah membawa secret. `details` disanitasi oleh Siamang.
4. Tidak ada endpoint yang menjalankan perintah langsung. Semua aksi lewat
   task dan approval.

## 7. Versioning & compatibility

* `contract_version = MAJOR.MINOR`. Versi `0.x` = draft; boleh berubah
  dengan pemberitahuan di dokumen ini.
* **MINOR**: hanya penambahan (field opsional, event type baru, error code
  baru).
* **MAJOR**: penghapusan/rename field, perubahan arti, field opsional menjadi
  wajib.
* Consumer **wajib mengabaikan** field dan event `type` yang tidak dikenal.
* Siamang menolak MAJOR yang tidak didukung dengan
  `UNSUPPORTED_CONTRACT_VERSION`.
* Prefix URL `/v0/` mengikuti MAJOR.
* Idempotensi: `request_id` yang sama menghasilkan `task_id` yang sama dan
  `duplicate: true`.
* Ordering: consumer mengurutkan berdasarkan `seq` per task, membuang
  duplikat `event_id`, dan replay via `after_seq`.

## 8. Di luar cakupan

* Multi-user pada kontrak ini (autentikasi HTTP ada di [HTTP_API.md](HTTP_API.md))
* Streaming token LLM ke klien
* Task bertingkat yang terlihat oleh klien
* Upload file dari klien
