# Onboarding — PITS Backend ("trustmark")

Panduan handoff untuk developer yang melanjutkan/mengambil alih project ini. Baca ini dulu sebelum menyentuh kode.

**Terakhir diupdate:** 2026-08-01

---

## 1. Apa Project Ini

Backend proof-of-concept untuk **Public Information Trust System (PITS)** — registrasi dokumen terautentikasi (hash SHA-256 + anchoring ke blockchain lokal Anvil) dan verifikasi publik integritas dokumen. Stack: FastAPI, PostgreSQL, Keycloak (OIDC/JWT), Web3/Anvil, SQLAlchemy, Dynaconf, `uv`.

Baca urutan berikut untuk konteks lengkap:
1. `README.md` — cara jalankan lokal, API summary, limitasi PoC.
2. `CLAUDE.md` — source of truth, constitution (aturan tidak boleh dilanggar), aturan kritis.
3. `_docs/brd/brd-core.md` — kenapa project ini ada, persona, journey.
4. `_docs/architecture/system-overview.md` — komponen & data flow.
5. `_docs/audit/audit-2026-07-31.md` — audit menyeluruh pertama (temuan gap, severity, lokasi file).

## 2. Setup Cepat

```bash
cp docker/.env.example docker/.env
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build -d
curl http://localhost:41012/health
```

Akun test: realm `nextjs-kc`, publisher `test-publisher` / `test` (lihat README § Local test accounts).

## 3. Yang Perlu Diketahui SEBELUM Coding

- **Jangan** isi file kosong di `api/v1/{exports,inventor,status,verification}.py`, `infra/{cache,reports,hash_generator}.py`, `services/*.py`, `models/documents.py` tanpa requirement baru yang eksplisit — ini scaffolding sisa rencana lama, bukan pola yang harus diikuti. Detail: `_docs/architecture/system-overview.md`.
- **`TEST_MODE`** bypass auth punya bug wiring: override hanya aktif kalau app dijalankan via `python -m trustmark.main` (blok `if __name__ == "__main__"`), TIDAK aktif kalau dijalankan via entry-point `start` (pyproject) atau `uvicorn` langsung. Cek ulang sebelum mengandalkan `TEST_MODE` untuk testing lokal/CI. Detail: `_docs/audit/audit-2026-07-31.md` #2.
- **Belum ada test** untuk `api/v1/documents.py` (register/verify/records) maupun `infra/auth/keycloak.py`. Kalau kamu mengubah salah satu, tambahkan test integrasi sekalian — jangan cuma ubah kode tanpa test.
- **Riwayat git mengandung `docker/.env.save`** (commit `a70d53b`) berisi credential lokal (password DB, Keycloak, private key Anvil deterministik). Belum dibersihkan dari history — lihat `_docs/tasks/tasks.md` #1. Isinya sama dengan `.env.example` (bukan rahasia produksi), tapi tetap perlu dibersihkan.
- Tidak ada CI aktif (lint/test) — workflow GitHub Actions sengaja dihapus untuk rilis UAT ini. Jangan asumsikan ada gate otomatis sebelum merge.

## 4. Alur Kerja yang Diharapkan

Ikuti `CLAUDE.md` § "Wajib Sebelum Coding Fitur". Ringkas: cek SRS di `_docs/srs/` dulu → cek Definition of Ready → coding → jalankan AI review checklist sebelum lapor selesai → update `_docs/status/log.md` (entry baru di paling atas, jangan hapus yang lama).

## 5. Prioritas Kerja Saat Ini

Lihat `_docs/tasks/tasks.md` untuk daftar lengkap dengan severity. Ringkas 3 teratas:
1. Bersihkan `docker/.env.save` dari git history + perbaiki `.gitignore`.
2. Perbaiki wiring `TEST_MODE` + tambahkan guard `ENVIRONMENT != production`.
3. Tambah test integrasi untuk endpoint register/verify dan modul auth Keycloak.

## 6. Kontak/Konteks Tambahan

Project ini adalah bagian dari dua repo terpisah — frontend ada di `../frontend-for-uat/` (perlu jalan bersamaan, lihat `frontend-for-uat/README.md`). Realm Keycloak & client secret harus tetap sinkron antara keduanya.
