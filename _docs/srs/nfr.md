# Non-Functional Requirements — PITS Backend

Berlaku global untuk seluruh project — bukan per-fitur.

## Timezone

Semua timestamp disimpan UTC di database (`created_at`, dsb). Konversi ke timezone lokal dilakukan di layer presentasi (frontend), bukan di backend.

## Keamanan & Privasi Data

- Backend tidak menyimpan isi dokumen lengkap — hanya hash, metadata registry, dan bukti transaksi blockchain.
- Kredensial (password DB, client secret Keycloak, private key blockchain) hanya melalui environment variable / Dynaconf — tidak pernah hardcoded di kode maupun ter-commit ke git (termasuk file backup `.env.save` dsb — lihat insiden di `_docs/audit/audit-2026-07-31.md`).
- Endpoint `register`/`records` wajib JWT + role `publisher`. Endpoint `verify` sengaja publik.
- `TEST_MODE` (bypass auth) hanya untuk lingkungan test/CI — wajib ada guard agar tidak bisa aktif di production.

## Availability & Error Handling

- Ini PoC — tidak ada target uptime formal. Untuk deployment berikutnya (di luar PoC), pertimbangkan health check container (`/health`) sebagai dasar readiness/liveness probe.
- Kegagalan koneksi blockchain (Anvil down) harus menghasilkan error 500 yang jelas, bukan silent failure atau record setengah tersimpan.

## Performance Targets

- Response register/verify di lingkungan lokal Anvil: target < 5 detik untuk register (termasuk anchoring), < 2 detik untuk verify.
- Tidak ada target khusus untuk volume data besar — registry saat ini diasumsikan skala kecil (PoC/UAT).

## Constraint Teknis

- Blockchain: Anvil lokal (development EVM chain) — reset setiap container state dihapus, bukan ledger permanen.
- Database: PostgreSQL tanpa migration tooling (Alembic dsb) — schema perubahan berarti drop & recreate volume untuk PoC ini.
- Python 3.12 (lihat `.python-version`), dependency dikelola via `uv` + `uv.lock` (frozen install di Docker image).
