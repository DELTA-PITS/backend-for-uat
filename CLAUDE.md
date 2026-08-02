# PITS Backend ("trustmark") — Claude Code Instructions

Backend proof-of-concept untuk **Public Information Trust System (PITS)**: registrasi dokumen terautentikasi (hash SHA-256 + anchoring ke blockchain lokal Anvil) dan verifikasi publik integritas dokumen. Stack: FastAPI, PostgreSQL, Keycloak (OIDC/JWT), Web3/Anvil, SQLAlchemy, Dynaconf, uv.

---

## Shortcut: Source of Truth

```
Fitur spesifik (teknis)    → _docs/srs/fr-document-registration.md, _docs/srs/fr-document-verification.md
Fitur spesifik (bisnis)    → _docs/brd/brd-core.md
Arsitektur                 → _docs/architecture/system-overview.md
Code style & project rules → _docs/referensi/coding-standards.md
Boleh mulai coding kalau   → _docs/quality/definition-of-ready.md
Definisi "selesai"         → _docs/quality/definition-of-done.md
Checklist sebelum lapor    → _docs/ai/review-checklist.md
Pekerjaan aktif            → _docs/tasks/tasks.md
Log progress per sesi      → _docs/status/log.md
Hasil audit awal           → _docs/audit/audit-2026-07-31.md
```

## Prinsip Tidak Bisa Diganggu Gugat (Constitution)

- Backend **tidak pernah** menyimpan file dokumen lengkap yang diupload — hanya hash SHA-256, metadata registry, dan bukti transaksi blockchain (lihat README.md § API summary). Jangan tambahkan penyimpanan file mentah tanpa keputusan eksplisit (butuh ADR baru).
- Private key blockchain, password DB, dan client secret Keycloak **tidak pernah** di-commit ke git dalam bentuk apapun — termasuk file `.env.save`/`.env.bak`/backup lain. Lihat insiden `docker/.env.save` di `_docs/audit/audit-2026-07-31.md` §2.
- `TEST_MODE=true` (bypass autentikasi) **tidak boleh** bisa aktif kecuali eksplisit di environment development/CI — tidak boleh ada jalur di mana `TEST_MODE` aktif tanpa guard `ENVIRONMENT != production`.
- Endpoint `register` dan `records` **selalu** wajib role `publisher` melalui `require_roles()` — tidak ada jalur bypass selain dependency override yang eksplisit di atas.
- Endpoint `verify` **tetap publik** (tanpa auth) sesuai desain PITS — jangan tambahkan auth requirement tanpa mendiskusikan dampaknya ke BRD.

Setiap Technical Design wajib dicek tidak melanggar bagian ini sebelum coding.

## Wajib Sebelum Coding Fitur

1. Cek fitur di `_docs/srs/` → baca SRS-nya.
2. Cek BRD di `_docs/brd/brd-core.md` → pahami konteks bisnis, persona, journey.
3. Jika belum ada → buat BRD + SRS dulu, update `_docs/README.md`.
4. Cek `_docs/quality/definition-of-ready.md` — semua item tercentang sebelum mulai coding.
5. Sebelum lapor selesai → jalankan `_docs/ai/review-checklist.md`.

## Aturan Kritis

- Port default backend: `41012` (docker-compose) — jangan bentrok dengan service lain.
- Semua konfigurasi lewat Dynaconf (`conf/settings.toml` + env var) — jangan hardcode nilai config di kode.
- Modul di `services/` (etherum_connector, github_connector, graphdb_connector, x_connector) dan sebagian besar `api/v1/` selain `documents.py`/`metrics.py` adalah **file kosong/dead code** peninggalan rencana arsitektur lama — jangan diisi tanpa konfirmasi requirement baru, dan jangan diasumsikan sebagai referensi pola yang harus diikuti.
- Ini masih tahap PoC (lihat README § limitasi) — tidak ada migration DB, mulai dari volume Postgres bersih untuk testing.

## Status Update (WAJIB)

Sebelum mengakhiri sesi kerja, **tambah entry baru di paling atas** `_docs/status/log.md` (jangan timpa/hapus entry lama) — format lihat `status-log.template.md` di playbook. Isi: progress %, yang diselesaikan sesi ini, blocker/keputusan yang dibutuhkan, next steps. Commit bareng perubahan kode.
