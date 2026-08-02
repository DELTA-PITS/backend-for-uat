# System Overview — PITS Backend

**Terakhir diupdate:** 2026-07-31

## Gambaran Umum

Backend FastAPI yang menyediakan 2 kapabilitas inti: registrasi dokumen terautentikasi dan verifikasi publik. Berjalan bersama 3 service pendukung via Docker Compose: PostgreSQL (registry), Keycloak (auth), Anvil (blockchain lokal).

```
┌─────────────┐      JWT (Bearer)      ┌──────────────────────────┐
│  Frontend   │ ─────────────────────► │   FastAPI (trustmark)    │
│  (Next.js)  │                        │   src/trustmark/main.py  │
└─────────────┘                        └────────────┬─────────────┘
                                                      │
                        ┌─────────────────────────────┼─────────────────────────────┐
                        │                              │                             │
                        ▼                              ▼                             ▼
               ┌────────────────┐          ┌─────────────────────┐        ┌──────────────────┐
               │   Keycloak     │          │     PostgreSQL        │        │   Anvil (EVM)     │
               │ (JWT/JWKS,     │          │  registry.models      │        │  local blockchain │
               │  role publisher)│          │  (record registry)   │        │  anchoring        │
               └────────────────┘          └─────────────────────┘        └──────────────────┘
```

## Komponen

| Komponen | File utama | Tanggung Jawab |
|---|---|---|
| Entry point aplikasi | `src/trustmark/main.py` | Setup FastAPI app, CORS, lifespan, mounting router, `/health` |
| Registrasi & verifikasi | `src/trustmark/api/v1/documents.py` | Endpoint register/records/verify — business logic utama |
| Health/metrics | `src/trustmark/api/v1/metrics.py` | Endpoint `/api/v1/health` dengan response model terdokumentasi |
| Autentikasi | `src/trustmark/infra/auth/keycloak.py` | Verifikasi JWT via JWKS, cek role, `require_roles()` dependency |
| Hashing | `src/trustmark/infra/hash_engine.py` | Generate SHA-256 dari byte dokumen |
| Blockchain | `src/trustmark/infra/blockchain_connector.py` | Membuat & mengirim transaksi anchoring ke Anvil |
| Database | `src/trustmark/infra/db.py`, `registry/models.py` | Koneksi SQLAlchemy, model ORM registry |
| Konfigurasi | `src/trustmark/infra/commons.py`, `conf/settings.toml` | Dynaconf settings loader |

## Modul yang Direncanakan Tapi Belum Diimplementasikan

File-file berikut ada di struktur folder tapi **kosong (0 byte)** — sisa rencana arsitektur yang lebih besar (multi-connector: GitHub, X/Twitter, GraphDB, Ethereum terpisah) yang tidak jadi diimplementasikan untuk rilis UAT ini:
`api/v1/{exports,inventor,status,verification}.py`, `infra/{cache,reports,hash_generator}.py`, `services/{etherum_connector,github_connector,graphdb_connector,x_connector}.py`, `utils/helper`, `models/documents.py`.

Jangan menganggap file-file ini sebagai referensi pola yang harus diikuti — lihat `_docs/audit/audit-2026-07-31.md` §1 untuk detail, dan jangan diisi tanpa BRD/SRS baru yang eksplisit mendefinisikan kebutuhannya.

## Data Flow Utama

Lihat detail di `_docs/srs/fr-document-registration.md` dan `_docs/srs/fr-document-verification.md`.

## Deployment

Semua service dijalankan via `docker/docker-compose.yml` (lokal) dan `.github/docker/docker-compose.yml` (versi CI — saat ini tidak dipakai karena workflow CI dihapus, lihat audit). Dua config ini punya perbedaan halus (healthcheck, user root vs non-root, strategi copy config) yang berisiko drift — lihat `_docs/audit/audit-2026-07-31.md` §4.
