# Status — PITS Backend

File ini diupdate Claude Code **setiap sesi kerja selesai**. Entry terbaru selalu ditambah di **paling atas** (append, jangan timpa/hapus entry lama).

---

## [2026-08-02 03:30] — Claude Code

- **Progress**: Audit logout dari sesi frontend (`frontend-for-uat/_docs/security/session-auth-audit-2026-08-02.md`) menemukan akar masalah "Invalid redirect uri" ada di konfigurasi Keycloak client `nextjs-web`, bukan di kode Next.js. `realms/realm-export.json` diperbarui; instance Keycloak yang sedang jalan (container `trustmark-keycloak-1`) BELUM ikut diperbaiki — perlu tindakan manual user (lihat blocker).
- **Selesai sesi ini**:
  - Ditemukan (via Keycloak Admin API, read-only): client `nextjs-web` di realm `nextjs-kc` cuma punya 1 `redirectUris` (`.../api/auth/callback/keycloak`, untuk login) dan atribut `post.logout.redirect.uris` bernilai `+` (artinya "ikuti persis `redirectUris`") — sehingga `post_logout_redirect_uri=http://localhost:3000/api/auth/logout` yang dikirim frontend saat logout SELALU ditolak Keycloak, karena tidak match satu-satunya URI yang terdaftar.
  - `docker/realms/realm-export.json`: client `nextjs-web` ditambah `http://localhost:3000/*` ke `redirectUris`, dan atribut `attributes.post.logout.redirect.uris` diisi eksplisit `http://localhost:3000/*` — supaya kalau container Keycloak di-rebuild dari volume kosong, konfigurasinya sudah benar sejak awal.
  - **Percobaan perbaikan langsung ke instance yang sedang jalan (via Admin REST API, pakai kredensial `KEYCLOAK_USER`/`KEYCLOAK_PASS` dari `.env`) diblokir oleh sistem** (kategori "modifikasi security/system settings" — di luar kewenangan AI meski di environment lokal). Token & file sementara langsung dihapus setelah itu.
- **Blocker / butuh keputusan dari Ersa**: instance Keycloak yang SEDANG JALAN (volume Postgres persisten, realm sudah ter-import sebelumnya) tidak otomatis ikut ter-update oleh perubahan file JSON ini — Keycloak `--import-realm` tidak menimpa realm yang sudah ada di DB. Untuk perbaikan SEGERA (tanpa restart/reset container), user perlu klik manual: admin console (`http://localhost:8080/admin`) → realm `nextjs-kc` → Clients → `nextjs-web` → tab Settings → field "Valid post logout redirect URIs" → tambah `http://localhost:3000/*` → Save.
- **Next steps**: setelah diperbaiki manual, uji ulang alur logout end-to-end dari frontend. Kalau nanti container di-rebuild dari volume bersih, `realm-export.json` yang sudah diperbaiki ini akan otomatis benar tanpa langkah manual lagi.

---

## [2026-08-01 00:00] — Claude Code

- **Progress**: Handoff dokumentasi selesai — belum ada perubahan kode.
- **Selesai sesi ini**:
  - Dibuat `ONBOARDING.md` (root) untuk developer yang akan melanjutkan project: setup cepat, hal-hal kritis yang harus diketahui sebelum coding (dead code, bug wiring `TEST_MODE`, gap test), prioritas kerja.
  - `_docs/README.md` diupdate — tambah link ke `ONBOARDING.md` di navigasi cepat.
  - Aturan baru (global, semua project): commit git tidak lagi menyertakan trailer `Co-Authored-By: Claude` — supaya riwayat commit tidak menampilkan atribusi AI di GitHub.
- **Blocker / butuh keputusan dari Ersa**:
  - Belum di-commit/push atas permintaan eksplisit — menunggu konfirmasi sebelum commit pertama untuk seluruh perubahan dokumentasi (`CLAUDE.md`, `_docs/`, `ONBOARDING.md`).
- **Next steps**:
  - Setelah dapat konfirmasi, commit dokumentasi ini (tanpa co-author Claude) sebelum mulai kerjakan backlog di `tasks/tasks.md`.

---

## [2026-07-31 00:00] — Claude Code

- **Progress**: Dokumentasi baseline `_docs/` selesai dibuat (retroaktif) — belum ada perubahan kode.
- **Selesai sesi ini**:
  - Audit menyeluruh codebase (`_docs/audit/audit-2026-07-31.md`) — 12 temuan, termasuk 1 Critical (secret ter-commit) dan 3 High.
  - Scaffolding `_docs/` lengkap: CLAUDE.md, BRD, SRS (registrasi + verifikasi + NFR), 2 ADR (blockchain lokal, Keycloak), architecture overview, coding standards, Definition of Ready/Done, AI review checklist.
  - Backlog awal (`tasks/tasks.md`) diisi dari temuan audit, diurutkan prioritas.
- **Blocker / butuh keputusan dari Ersa**:
  - `docker/.env.save` masih ada di git history — perlu keputusan/izin untuk membersihkan history (destructive operation, butuh konfirmasi eksplisit sebelum dieksekusi).
- **Next steps**:
  - Mulai kerjakan backlog dari prioritas tertinggi (task #1–#5 di `tasks/tasks.md`).

---

<!-- Entry lama di bawah sini, urut dari terbaru ke terlama -->
