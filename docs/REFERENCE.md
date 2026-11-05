# Referensi Siamang

Rincian yang dipindahkan dari README agar README tetap ringkas. Untuk cara memasang, lihat [Quick Start](../README.md#quick-start).

## Keputusan di dua gerbang

Pada konfigurasi bawaan, merge tetap di tangan Anda. Kedua keputusan memakai body yang sama:

```bash
api /tasks/TASK_ID/design-decision -d '{"decision": "approve", "reason": "Desain sesuai kebutuhan."}'
api /approvals/RUN_ID -d '{"decision": "approve", "reason": "Bukti sudah saya periksa."}'
```

## Tim agen

| Agen | Tugas | Memakai model |
|---|---|---|
| **Amang (Lead)** | Bertanya, menyusun brief, melapor | Ya |
| **Planner** | PRD, roadmap, WBS, acceptance criteria | Ya |
| **Arsitek** | SRS, arsitektur, diagram, pembagian paket kerja per gelombang | Ya |
| **Programmer** | Menulis patch per paket, dengan beberapa paket sekaligus dalam satu gelombang | Ya |
| **QA** | Pemeriksaan statis yang menentukan; model hanya menulis rencana test | Sebagian |
| **Reviewer** | Pemeriksaan deterministik yang menentukan; ulasan model bersifat saran | Sebagian |

Di konfigurasi, Arsitek bernama `analyst`.

## Bukti yang bisa Anda periksa

| Sumber | Ditulis oleh | Aturan |
|---|---|---|
| `authored` | Model | Draf atau pendapat, tidak pernah dianggap bukti |
| `derived` | Kode | Termasuk putusan QA dan Reviewer; model tidak bisa mengubahnya |
| `measured` | Eksekusi nyata | Model tidak ditanya; tetap `not_run` sampai hasil ukurnya ada |

Bukti dan keputusan disegel dengan HMAC. Saat Anda menyetujui, server memuat artefak miliknya sendiri, menghitung ulang
rantai bukti, dan menolak yang berubah. Klien tidak bisa mengirim patch, status lulus, atau segel.

## Pekerjaan besar

Satu panggilan model tidak cukup untuk pekerjaan besar seperti upgrade framework. Siamang memecahnya, dan mencatat apa
yang benar-benar terjadi.

| Kondisi | Yang dilakukan Siamang | Bukti yang bisa dilihat |
|---|---|---|
| Satu tahap desain punya banyak dokumen | Maksimal 3 dokumen per panggilan, 3 panggilan bersamaan | `design_parts`: dokumen, jam mulai dan selesai, status tiap panggilan |
| Panggilan desain gagal | Hanya bagian yang gagal diulang, sampai 3 putaran dengan waktu 300, 450, lalu 600 detik | Event riwayat membawa `attempt`, `retry_reason`, `remaining` |
| Task gagal lalu diantrekan ulang | Dokumen yang sudah jadi dipakai ulang, selama instruksi, spesifikasi, dan jawaban Anda sama | Event riwayat membawa `reused` |
| Pekerjaan menyentuh banyak file | Arsitek membaginya menjadi paket per gelombang: satu gelombang paralel, gelombang berikutnya melihat hasilnya | `work_packages` dan `task.packages` dengan `wave` |
| Paket timeout | Dicoba sekali lagi, lalu dibelah dua menurut path miliknya | `attempt` pada paket, dan judul "(a)" dan "(b)" |
| Pembagian kerja gagal | Model ditanya sekali lagi beserta alasannya; bila tetap gagal, statusnya `failed`, tidak pernah dilaporkan sebagai "cukup satu Programmer" | Dokumen `work_packages`, dan "bagi ulang" di gerbang desain |

Paket yang digabung dijaga dengan aturan path yang diperiksa kode, kontrak bersama yang diterima setiap paket, dan satu
putaran QA serta Reviewer atas hasil gabungan. Jalur paket sejauh ini baru diuji dengan model tiruan; batasnya ada di
[PHASE_ROADMAP.md](PHASE_ROADMAP.md).

## Konfigurasi

Config dibaca dari `--config FILE` atau `SIAMANG_CONFIG`, dan bisa ditimpa per key dengan `SIAMANG_<SECTION>_<KEY>`.
Contoh lengkapnya ada di [siamang.example.toml](../siamang.example.toml). Database bawaan ada di `~/.siamang/siamang.db`.

```toml
[providers.claude]
type = "claude_cli"
timeout_secs = 600            # tanpa ini satu panggilan dibatasi 120 detik; 600 adalah batas atasnya

[agents.programmer]
model = "claude-sonnet"
parallel = 3                  # paket kerja bersamaan per gelombang (1-6)
ci_repair = 2                 # berapa kali CI gagal kembali ke Programmer (0-3, 0 = mati)
```

Waktu satu panggilan adalah yang terkecil dari batas tahapnya, `timeout_secs` provider, dan 600 detik. Nilai di bawah 600
membuat percobaan ulang tahap desain tidak mendapat waktu lebih.

| Provider | Untuk |
|---|---|
| `claude_cli` | Login CLI `claude` Anda, untuk pemakaian pribadi |
| `ollama` | Model lokal; pilih model yang sudah terpasang |
| `echo` | Hanya untuk menguji sambungan; ditolak oleh HTTP API |

Jika model belum dikonfigurasi, run ditolak, bukan berpura-pura bekerja.

Sandbox dan Recovery Engine mati secara bawaan. Cara menyalakannya ada di [SANDBOX.md](SANDBOX.md) dan
`siamang.example.toml`.

## HTTP API

Semua route `/v0` butuh bearer token dan izin yang sesuai. Referensi lengkap ada di [HTTP_API.md](HTTP_API.md),
dan kontrak OpenAPI ada di `GET /v0/openapi.json`.

| Method | Route | Fungsi |
|---|---|---|
| GET | `/v0/health`, `/v0/capabilities` | Kesehatan server, model, dan kemampuan yang aktif |
| GET | `/v0/stream` | Event stream SSE: perubahan saat terjadi, bisa dilanjutkan setelah koneksi putus |
| POST | `/v0/projects` | Mendaftarkan dan meng-clone repo GitHub |
| POST | `/v0/projects/{id}/intake/messages`, `.../intake/approve` | Spesifikasi proyek bersama Lead |
| POST | `/v0/projects/{id}/drafts`, `.../drafts/{draft}/confirm` | Percakapan sebelum task, lalu memulai task |
| GET | `/v0/tasks/{id}`, `.../documents`, `.../reports`, `.../execution-events` | Status, dokumen agen, laporan, dan riwayat |
| POST | `/v0/tasks/{id}/design-decision` | Gerbang desain |
| GET, POST | `/v0/approvals`, `/v0/approvals/{run_id}` | Gerbang akhir |
| POST | `/v0/tasks/{id}/messages`, `.../retry`, `.../cancel`, `.../publish` | Melanjutkan, mengulang, membatalkan, menerbitkan ulang |

## CLI

CLI untuk inspeksi dan pengelolaan. Pekerjaan agen dijalankan lewat HTTP API.

```bash
siamang health | validate [pesan.json]         # kesehatan dan validasi kontrak
siamang models --check                         # provider dan model
siamang projects register --name NAMA --path PATH --type web
siamang projects [--all] [--json]              # juga: get, update, archive, restore
siamang git inspect|status|diff|worktrees PRJ  # Git read-only
siamang tasks [--project PRJ] [--status S]     # juga: get, recovery, cancel, retry
siamang replay TSK                             # replay event task
siamang tools | agents                         # katalog
siamang orchestrator | planner | analyst | programmer | qa | reviewer | loop | approval
                                               # menampilkan kebijakan, tidak menjalankan apa pun
```

## Menguji Siamang sendiri

```bash
.venv/bin/pytest                               # test offline, tanpa LLM
.venv/bin/python scripts/smoke_http.py         # HTTP dengan server dan database sementara
```
