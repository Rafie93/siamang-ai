# Dokumentasi Siamang

**Evidence-Based Autonomous Software Engineering.** Setiap klaim ada buktinya.

Mulai dari sini sesuai kebutuhan Anda.

| Anda ingin | Baca |
|---|---|
| Memahami tujuan, posisi produk, dan status tiap komponen | [VISION.md](VISION.md) |
| Melihat urutan pekerjaan | [PHASE_ROADMAP.md](PHASE_ROADMAP.md) |
| Memahami struktur sistem dan keputusan desain | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Memakai atau mengintegrasikan API | [HTTP_API.md](HTTP_API.md) |
| Memberi panduan kepada agen AI yang bekerja dengan Siamang | [SKILL.md](SKILL.md) |

## Antarmuka

| Dokumen | Isi |
|---|---|
| [HTTP_API.md](HTTP_API.md) | Autentikasi, route, alur task, proyek GitHub, dokumen agen, CI |
| [CONTRACT.md](CONTRACT.md) | Envelope pesan, event lifecycle, versioning |

## Inti

| Dokumen | Isi |
|---|---|
| [MODEL_LAYER.md](MODEL_LAYER.md) | Provider, model, capability, router |
| [PROJECT_REGISTRY.md](PROJECT_REGISTRY.md) | Identitas dan lifecycle project |
| [GIT_WORKSPACE.md](GIT_WORKSPACE.md) | Lapisan Git read-only dan repo terlindungi |
| [TOOL_REGISTRY.md](TOOL_REGISTRY.md) | Definisi tool, izin deny-by-default |
| [TASK_ENGINE.md](TASK_ENGINE.md) | Lifecycle task, persistensi, event, retry |

## Agen dan alur kerja

| Dokumen | Isi |
|---|---|
| [AGENT_INTERFACE.md](AGENT_INTERFACE.md) | Kontrak agent |
| [ORCHESTRATOR.md](ORCHESTRATOR.md) | Koordinasi satu agent |
| [PLANNER.md](PLANNER.md) | Rencana dan graf dependensi |
| [SYSTEM_ANALYST.md](SYSTEM_ANALYST.md) | Analisis project |
| [PROGRAMMER.md](PROGRAMMER.md) | Usulan perubahan kode |
| [QA.md](QA.md) | Verifikasi statis |
| [REVIEWER.md](REVIEWER.md) | Penilaian independen |
| [LOOP.md](LOOP.md) | Loop Programmer, QA, dan Reviewer |
| [APPROVAL.md](APPROVAL.md) | Keputusan manusia dan rantai bukti |

## Aturan menjaga dokumen

* Dokumen komponen menjelaskan kode hari ini. Rencana masuk ke
  [VISION.md](VISION.md) atau [PHASE_ROADMAP.md](PHASE_ROADMAP.md).
* Perubahan perilaku diperbarui bersama dokumennya.
* Jika dokumen dan kode berbeda, kode yang benar dan dokumen yang diperbaiki.
