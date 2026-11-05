# Antarmuka web

Kode ada di folder [`web/`](../web), dan semua path di bawah relatif terhadap folder itu.

Browser → gateway lokal (`scripts/local.mjs`, session + CSRF, token acak di server) → SIAMANG HTTP `/v0` → model yang dipilih per agen di `siamang.toml`. Frontend tidak mengimplementasikan ulang Planner/Analyst/Programmer/QA/Reviewer/Loop/Approval.

## Lapisan

| Lapisan | Berkas | Tanggung jawab |
|---|---|---|
| Adapter | `src/services/api.js`, `contract.js` | fetch + timeout, CSRF, validasi bentuk respons |
| Domain | `src/domain/status.js` | label status task yang jujur (heartbeat ≤15 dtk) |
| Domain | `src/domain/team.js` | roster 6 agen, peta fase→agen, `teamState()` dan `transitions()` dari execution-events |
| Domain | `src/domain/demo.js` | timeline simulasi berlabel `simulated:true` |
| Domain | `src/domain/documents.js` | gabungan katalog dokumen backend dengan yang tercatat; entri tanpa catatan tampil "belum sampai tahap ini" |
| 3D | `src/three/office.js` | renderer, kamera, cahaya, loop, overlay label/balon, picking, follow, lifecycle/dispose |
| 3D | `src/three/environment.js` | lantai, dinding berjendela, workstation, kiosk approval, papan pipeline, pantry, server rack |
| 3D | `src/three/character.js` | karakter prosedural bersendi; aksi `sit/stand/walk` + gestur (`type`, `talk`, `listen`, `present`, `think`, `celebrate`, …) dengan blending teredam |
| 3D | `src/three/director.js` | koreografi: serah-terima, approval, selebrasi, aktivitas santai, reset saat ganti task |
| 3D | `src/three/navigation.js` | denah, jalur meja→lane→lorong→lane→tujuan, uji tabrakan furnitur |
| 3D | `src/three/screens.js` | tekstur monitor per peran dan papan pipeline (CanvasTexture) |
| UI | `src/stores/office.js` | store Pinia: pemuatan per bagian, state task/proyek, dan aksi ke backend |
| UI | `src/services/stream.js`, `src/domain/live.js` | koneksi event stream; arti tiap event bagi halaman: bagian yang dimuat ulang, penggabungan riwayat, heartbeat |
| UI | `src/router/index.js` | daftar halaman, route, dan item sidebar |
| UI | `src/layouts/`, `src/pages/`, `src/components/` | shell aplikasi, satu halaman per menu, panel tim, log komunikasi, kontrol scene |
| UI | `src/components/ui/` | komponen kecil di atas elemen native (`<dialog>`, `<select>`, `<label>`), tanpa library UI |
| Tema | `src/assets/theme.css`, `src/composables/useTheme.js` | token warna terang/gelap dan pilihan tema |

## Aliran data

Event stream (`/v0/stream`) → `scopesFor()` memilih bagian yang berubah → `load()` memuat ulang bagian itu saja → `selected` task + `execution-events` → `teamState()` (dihitung ulang tiap detik agar heartbeat kedaluwarsa lokal) → `office.setTeam()` → director.

Tahap baru langsung masuk riwayat dari event-nya. Heartbeat langsung memperbarui `execution` tanpa request; tanpa ini, tim tampak "belum terverifikasi" setiap 15 detik karena halaman tidak lagi bertanya. Bila worker benar-benar berhenti, heartbeat berhenti dan label itu muncul dengan benar.

Polling tetap ada sebagai jaring pengaman:

| Keadaan | `refresh()` penuh | Label di bilah atas |
|---|---|---|
| Stream terbuka | Tiap 30 detik | Live |
| Stream terputus, ditolak, atau browser tanpa `EventSource` | Langsung, lalu tiap 3 detik, dengan backoff saat gagal | Polling |
| Tab tersembunyi | Tiap 30 detik; stream ditutup, karena browser hanya memberi enam koneksi per host | Tidak terlihat |

Stream hanya memberi tahu. Nilai yang ditampilkan selain tahap dan heartbeat tetap berasal dari route HTTP, dengan validasi yang sama. Setiap `ready` memicu `refresh()` penuh, jadi yang terlewat saat koneksi putus ikut termuat.

`loadEvents()` membaca riwayat per halaman sampai habis, dan melanjutkan dari posisi route terakhir, bukan dari event terbaru yang dipegang. Event dari stream bisa saja lebih maju dari yang pernah terbaca lewat route, dan celahnya terisi pada pemuatan berikutnya.

Aturan director:
- **Bekerja** hanya untuk `team.activeAgent` (fase aktif terverifikasi).
- **Serah-terima** hanya dari transisi event yang tersimpan. Saat pertama melihat task yang masih berjalan, hanya transisi terakhir yang diputar ulang. Antrean dibatasi ke transisi terbaru agar animasi tidak tertinggal dari pipeline nyata; riwayat lengkap tetap ada di log teks.
- **Aktivitas santai** (kopi, whiteboard, jendela, peregangan) hanya untuk agen yang bagiannya selesai, atau saat tim idle. Agen dipanggil kembali bila dibutuhkan serah-terima. Statusnya tetap "Standby"/"Selesai".
- **Ganti task** → semua agen kembali ke kursi.
- **Reduced motion** → tanpa berjalan; serah-terima ditampilkan sebagai balon, loop render 2 fps.

## Performa

Geometri unit bersama (box/sphere/cylinder) diskalakan per mesh, material di-cache, monitor hanya digambar ulang saat status berubah atau ±5 fps ketika agen bekerja. Loop berhenti saat tab tersembunyi. Kualitas "Hemat GPU" mematikan bayangan dan memakai pixel ratio 1. Chunk `office` ±50 kB (17 kB gzip) di luar `three`.

## Data lokal

Database, kunci segel, dan clone proyek ada di `~/.siamang/web`, atau di folder yang ditunjuk `WEB_DATA`. Folder ini sengaja di luar repo: proyek tidak boleh berada di dalam repo Siamang sendiri (lihat [GIT_WORKSPACE.md](GIT_WORKSPACE.md#8-protected-repositories-architecture-6)).
