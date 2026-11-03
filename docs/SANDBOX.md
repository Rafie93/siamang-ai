# Execution Sandbox dan Verification (Phase 25–26)

Kode proyek dan patch dari Programmer tidak pernah berjalan di host. Dengan `[sandbox] enabled = true`, hasil yang
lolos QA dan Reviewer baru boleh menunggu persetujuan setelah test dan build berjalan di container.

## Yang dijamin setiap run

- Salinan proyek + patch di direktori sementara; proyek asli tidak ditulis. `.git`, file mirip secret, symlink, dan
  `node_modules` tidak ikut disalin.
- `--network none`, `--cap-drop ALL`, `no-new-privileges`, root filesystem read-only, user 65534, batas memori/CPU/proses.
- Environment container tetap dan minimal. Proses `podman`/`docker` di host hanya menerima `PATH`, `HOME`, dan
  variabel socket, jadi `ANTHROPIC_API_KEY` dan kunci segel tidak sampai ke sana.
- Perintah berupa daftar argumen, tanpa shell. Output dibatasi dan disensor sebelum disimpan.
- Perintah berasal dari konfigurasi operator, bukan dari repo, supaya patch tidak bisa melemahkan perintahnya.

## Aturan lulus

`passed` hanya jika setiap check keluar dengan kode 0. Engine tidak ada, perintah tidak dikonfigurasi, timeout, atau
gagal tidak pernah lulus. Check yang gagal kembali ke Programmer sebagai `verification_failure` sebanyak
`verify_attempts` kali, lalu run berhenti di `quality_gate` dengan alasan yang terlihat. Laporan ada di
`report.verification` pada run.

## Agen di dalam sandbox (Phase 27)

`[sandbox] tools = true` membuat Programmer membaca file, mengedit, dan menjalankan perintah di sandbox ini dalam batas
`tool_turns` dan `tool_runs`, lalu mengusulkan hasilnya. Berbeda dari check verifikasi, perintah di sini dipilih agen,
bukan operator; karena itu setiap perintah memakai sandbox yang sama (tanpa jaringan, tanpa secret, batas sumber daya), dan
hasil akhirnya tetap harus lolos check operator sebelum bisa menunggu persetujuan. Detail ada di PHASE_ROADMAP.md.

## Batas yang jujur

- Jaringan mati, jadi dependensi harus sudah ada di image (`podman pull` dulu).
- Acceptance criteria dicantumkan di laporan, tetapi `verified` hanya bila sebuah check dipetakan ke kriteria itu.
  Pemetaan otomatis belum ada, jadi hari ini semuanya `not_verified`.
- Verifikasi ulang setelah gagal hanya untuk run satu Programmer; run paket paralel yang gagal langsung berhenti.
- Di Linux dengan SELinux, bind mount mungkin butuh label `:Z`; belum ditangani.
