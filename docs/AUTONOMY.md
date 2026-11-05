# Otonomi: gerbang berbasis risiko dan Lead otonom (Phase 29–30)

Semua ini mati secara bawaan. Tanpa `[autonomy]`, setiap gerbang tetap menunggu pemilik dan Lead tidak menjawab apa pun.
Level hanya datang dari file operator. Tidak ada request API atau jawaban model yang bisa mengubahnya.

```toml
[autonomy]
level = 2                # 0 sampai 3
lead = true              # Phase 30
lead_answers = 6         # pertanyaan yang dijawab Lead per task (0-20)
lead_revisions = 1       # berapa kali Lead mengembalikan pekerjaan per task (0-3)
merge_method = "squash"  # level 3: squash, merge, atau rebase
```

Level 2 dan 3 butuh `sandbox.enabled` dan `sandbox.test_command`. Tanpa itu konfigurasinya ditolak saat dimuat, karena
gerbang pull request tidak akan pernah punya bukti test.

## Level

| Level | Desain | Pull request | Merge |
|---|---|---|---|
| 0 (bawaan) | pemilik | pemilik | pemilik |
| 1 | otomatis jika risiko rendah | pemilik | pemilik |
| 2 | otomatis jika rendah atau sedang | otomatis jika rendah atau sedang, dan test lulus | pemilik |
| 3 | sama dengan level 2 | sama dengan level 2 | otomatis jika rendah dan CI lulus |

Risiko tinggi selalu menunggu pemilik, di level berapa pun.

## Syarat tiap gerbang

Selain tabel di atas:

**Desain.** Pembagian kerja tidak boleh berstatus `failed`.

**Pull request.** Tiga syarat, ditambah bukti persetujuan yang sudah ada:

- Perintah `test` operator lulus di sandbox. Build yang lulus saja tidak dihitung sebagai test.
- Task punya repositori.
- Tidak ada kompromi dari penasihat atau replan Recovery Engine (tier 2 ke atas).

Hasilnya tetap lewat `ApprovalService` yang sama dengan keputusan manusia, atas nama `policy.autonomy`. Rantai bukti, QA, dan
Reviewer tetap dihitung ulang, dan tidak ada override.

**Merge.** Dinilai dari kondisi GitHub saat itu, bukan dari ingatan Siamang:

- Ada minimal satu run CI berkesimpulan `success`, dan tidak ada yang gagal. Build yang seluruhnya `skipped` bukan bukti.
- Repo tanpa workflow tidak pernah di-merge otomatis.
- PR terbuka, bukan draft, menuju branch utama, dan head-nya sama dengan commit yang diuji.
- Tidak ada peninjau yang meminta perubahan.
- Seluruh branch berisiko rendah: semua penilaian patch tiap task di branch itu (termasuk revisi dan perbaikan CI), ditambah
  daftar file PR dari GitHub. Task tanpa penilaian dihitung berisiko tinggi.

Perintah merge memakai `--match-head-commit`, jadi GitHub menolaknya bila branch bergerak. Branch protection tetap berlaku,
karena `--admin` tidak dipakai. Branch tidak dihapus.

## Cara risiko dinilai

Deterministik (`siamang.autonomy.risk`). Model tidak ikut menilai.

| Risiko | Pemicu |
|---|---|
| Tinggi | Path: autentikasi, izin, sesi, migrasi, skema, SQL, rahasia, `.env`, kripto, workflow CI, `CODEOWNERS`, deploy, infrastruktur, pembayaran |
| Tinggi | Baris yang ditambahkan: DDL SQL, password, JWT, OAuth, API key, HMAC, bcrypt, `eval`/`exec`, `subprocess` |
| Tinggi | Patch yang tampak membungkam test: menambah skip, menghapus test, mengurangi assertion, mengubah konfigurasi CI |
| Tinggi | Penilaian itu sendiri gagal berjalan |
| Sedang | Manifest dependensi, lockfile, `Dockerfile`, `Makefile` |
| Sedang | Lebih dari 20 file, 600 baris, 5 file dihapus, atau 4 paket kerja |

Penilaian terjadi dua kali. Sebelum kode ada, yang tersedia hanya brief pemilik dan path paket, jadi hasilnya perkiraan. Setelah
patch ada, penilaian dibuat dari isi patch itu, dan itulah yang menentukan. Brief berisiko rendah tidak bisa meloloskan patch
berisiko. Dokumen desain tulisan model sengaja tidak dibaca: hampir semuanya menyebut keamanan, dan dokumen yang diam tidak
membuktikan apa-apa.

## Lead otonom

Semua jalan ke pemilik kini lewat satu pintu (`Runtime._stop_for_owner`). Dengan `lead = true`, Lead mengerjakan dua hal di sana.

**Menjawab pertanyaan** yang datang dari Lead sendiri, pembagian kerja, atau Programmer. Jawaban hanya sah bila:

- Mengutip kata demi kata dari spesifikasi yang sudah disetujui, instruksi task, atau jawaban pemilik. Kode yang memeriksa
  kutipannya. Dokumen desain dan isi repo bukan sumber.
- Kutipannya minimal 15 karakter.
- Pertanyaannya tidak menyangkut hal yang selalu milik pemilik: topik berisiko tinggi, biaya, lisensi, privasi, atau penghapusan.
  Pertanyaan seperti ini tidak dikirim ke model sama sekali.
- Pertanyaan yang sama belum pernah dijawab Lead, dan jatah `lead_answers` masih ada.

Yang tidak memenuhi syarat diteruskan ke pemilik beserta `lead_reason`. Bila semua terjawab, run lanjut di worker yang sama.
Jawaban selalu tampil sebagai pesan Lead, lengkap dengan kutipannya.

**Memilih langkah berikutnya** saat QA atau Reviewer menahan hasil: `revise` dengan satu arahan, atau `ask_owner`. Pilihan ini
hanya ditawarkan bila kerja tambahan memang bisa membantu. Sandbox yang tidak tersedia, perbaikan CI yang ditahan, dan run yang
sudah menghabiskan Recovery Engine langsung ke pemilik. Arahan yang melemahkan test atau pemeriksaan ditolak. Temuan yang sama
setelah revisi tidak dikembalikan lagi.

Lead tidak punya jalan ke gerbang: ia tidak bisa menyetujui, membuka PR, atau merge. Model yang gagal berarti pertanyaannya
diteruskan ke pemilik.

Tidak berubah: percakapan sebelum task ada (intake dan draft), karena pemilik memang sedang hadir di sana.

## Catatan keputusan

`GET /v0/tasks/{id}/autonomy` mengembalikan setiap keputusan: gerbang, level, risiko, hasil, aktor, alasan, bukti, dan waktu.
Tabelnya append-only dan tiap baris disegel HMAC. `seal_valid` menunjukkan apakah segelnya masih cocok. Niat merge ditulis sebelum
merge dijalankan, jadi tidak ada merge tanpa catatan.

| `gate` | `outcome` | Aktor |
|---|---|---|
| `design`, `publish`, `merge` | `auto`, `held` | `policy.autonomy` (`policy.ci-repair` untuk perbaikan CI) |
| `merge` | `merge_failed` | `policy.autonomy` |
| `question` | `answered`, `forwarded` | `lead.autonomy` |
| `next_step` | `revise`, `forwarded` | `lead.autonomy` |

Identitas API tidak boleh memuat titik, jadi tidak ada token yang bisa bertindak atas nama aktor ini. Deskripsi PR menyebut siapa
yang menyetujui, dan tidak lagi mengatasnamakan operator untuk keputusan kebijakan.

## Batas yang jujur

- Diuji dengan model tiruan, mesin container tiruan, dan API GitHub tiruan (Git-nya nyata). Belum pernah berjalan dengan model
  nyata, container nyata, atau `gh pr merge` sungguhan. Jangan memakai level 3 pada repo penting sebelum itu terbukti.
- Penilaian risiko adalah heuristik berbasis nama dan pola. Kode berbahaya yang namanya biasa bisa lolos, dan alarm palsu akan
  sering muncul pada proyek yang banyak mengurus login. Level 2 dan 3 tidak menggantikan peninjauan kode.
- Kutipan membuktikan sumbernya ada, bukan bahwa jawabannya benar-benar mengikuti dari kutipan itu.
- "Test lulus" berarti perintah test operator keluar dengan kode 0. Pencocokan acceptance criteria belum ada (Phase 26), dan test
  yang ditulis agen untuk patchnya sendiri ikut dihitung.
- Level berlaku untuk semua proyek, belum per proyek.
- Run terputus dan batas langganan model masih menunggu tombol Lanjutkan dari pemilik, walaupun itu bukan keputusan.
- `/v0/tasks/{id}/work-division` tidak melewati Lead, karena dipicu pemilik sendiri di gerbang desain.
- Antarmuka web belum menampilkan catatan keputusan dan belum punya label untuk fase baru `deciding`. Ia menampilkan nama fasenya apa adanya.
