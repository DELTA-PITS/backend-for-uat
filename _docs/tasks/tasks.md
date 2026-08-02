# Tasks — Backlog (PITS Backend)

Semua request masuk sini. Diedit manual oleh manusia saat brief baru masuk; status diupdate seiring pengerjaan.

**Terakhir diupdate:** 2026-08-02

## Belum Dikerjakan

Backlog di bawah diturunkan dari `_docs/audit/audit-2026-07-31.md` (audit menyeluruh pertama, 2026-07-31), diurutkan dari severity tertinggi.

| # | Task | Tipe | Prioritas | Sumber |
|---|------|------|-----------|--------|
| 1 | Hapus `docker/.env.save` dari git working tree + history, perbaiki pola `.gitignore` (`docker/.env*`) | Security fix | Critical | Audit #1 |

## Sedang Dikerjakan

_(kosong)_

## Selesai

| # | Task | Tipe | Prioritas | Sumber | Catatan |
|---|------|------|-----------|--------|---------|
| 2 | Perbaiki wiring `TEST_MODE` bypass agar konsisten terlepas dari cara start aplikasi; tambahkan guard `ENVIRONMENT != production` | Security fix | High | Audit #2 | Dikerjakan 2026-08-02, belum di-commit |
| 3 | Aktifkan kembali CI minimal (lint + unit test) sebelum lanjut dari tahap UAT | Infra | High | Audit #3 | File workflow dibuat (`.github/workflows/check-linting-on-pr.yml`, `run-unit-tests-on-pr.yml`) tapi **TIDAK di-commit** — `.gitignore:216-217` sengaja meng-ignore `.github/workflows/`, sejalan dengan commit `95464d2`/`4b94c81` yang eksplisit menghapus CI dari repo UAT ini. Butuh konfirmasi eksplisit dari Ersa sebelum diaktifkan (override gitignore) |
| 4 | Tambah test integrasi untuk `api/v1/documents.py` (register/verify/records) | Test coverage | High | Audit #4 | idem |
| 5 | Tambah test untuk `infra/auth/keycloak.py` (verifikasi JWT, role) | Test coverage | High | Audit #4 | idem |
| 6 | Bersihkan dead code (10+ file 0 byte) atau beri catatan jelas "not implemented" | Refactor | Medium | Audit #5 | idem |
| 7 | Tambahkan healthcheck untuk service app di `docker/docker-compose.yml` | Infra | Medium | Audit #6 | idem |
| 8 | Manfaatkan Dynaconf `environments` untuk pemisahan config dev/staging/prod yang sesungguhnya | Infra | Medium | Audit #7 | idem |
| 9 | Pindahkan `ruff` ke dev-dependency, tambahkan config `[tool.ruff]`, evaluasi kebutuhan `sqlalchemy-orm` | Code quality | Medium | Audit #8 | idem |
| 10 | Perbaiki bug logika assertion (`is not str`) di `tests/locust/locustfiles/verify.py` | Bug fix | Low | Audit #9 | idem |
| 11 | Hapus duplikasi endpoint `/health`, hapus `print()` debug di lifespan | Code quality | Low | Audit #10 | idem |
| 12 | Perketat CORS `allow_methods`/`allow_headers` sesuai kebutuhan aktual (GET/POST saja) | Security hardening | Low | Audit #11 | idem |
| 13 | Rapikan duplikasi `docker/` vs `.github/docker/` (satu sumber kebenaran) | Infra | Low | Audit #12 | Cuma komentar basi dibersihkan — penggabungan penuh butuh ADR, dua Dockerfile menargetkan environment berbeda (lokal vs GH Actions runner) |
