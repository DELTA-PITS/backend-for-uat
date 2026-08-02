# Design Doc — Registrasi Dokumen

**Status:** Implemented
**Author:** Tim DELTA-PITS (didokumentasikan ulang oleh Claude Code)
**Reviewer:** —
**Tanggal:** 2026-07-31
**Berkaitan dengan BRD:** `_docs/brd/brd-core.md`
**Definition of Ready:** ✅ terpenuhi (retroaktif — fitur sudah live di rilis UAT)

---

> ⚠️ Sebelum menulis dokumen ini, cek bagian "Prinsip Tidak Bisa Diganggu Gugat" di `CLAUDE.md` project ini.

## TL;DR

Publisher terautentikasi (role `publisher`) meng-upload dokumen; backend menghitung SHA-256 di memory (tidak menyimpan file), meng-anchor hash ke blockchain lokal Anvil, dan menyimpan record registry di PostgreSQL. Publisher bisa melihat daftar record via endpoint terpisah.

---

## Context

Diimplementasikan di `src/trustmark/api/v1/documents.py`. Bergantung pada `infra/hash_engine.py` (hashing), `infra/blockchain_connector.py` (anchoring transaksi), `infra/auth/keycloak.py` (verifikasi JWT + role), dan `registry/models.py` (ORM record). Lihat `_docs/architecture/system-overview.md` untuk gambaran komponen penuh.

---

## Goals & Non-Goals

**Goals:**
- Menghasilkan bukti anchoring blockchain untuk setiap dokumen yang didaftarkan.
- Membatasi akses hanya untuk user dengan role `publisher`.
- Tidak menyimpan isi dokumen lengkap.

**Non-Goals:**
- Validasi konten/kebenaran dokumen (hanya integritas byte).
- Versioning dokumen (setiap upload adalah registrasi baru, tidak ada update record).

---

## Arsitektur & Data Flow

```
Publisher (JWT) → POST /api/v1/register → require_roles("publisher")
  → validasi file (tidak kosong, ≤ MAX_UPLOAD_BYTES)
  → hash_engine.generate_hash(bytes) → SHA-256
  → blockchain_connector.create_transaction(hash, ...) → tx hash Anvil
  → registry.models → simpan record (hash, tx_hash, issuer, filename, timestamp)
  → Response 200 { record_id, content_hash, transaction_hash, ... }
```

## Perubahan API / Interface

```
METHOD   : POST
URL      : /api/v1/register
Auth     : Bearer token (Keycloak JWT) + role "publisher"

Request  : multipart/form-data, field file

Response 200:
{
  "record_id": "uuid",
  "content_hash": "sha256 hex",
  "transaction_hash": "0x...",
  "issuer_id": "uuid",
  "filename": "string",
  "created_at": "ISO 8601"
}

Response 400: file kosong atau melebihi MAX_UPLOAD_BYTES
Response 401/403: token tidak valid / role bukan publisher
Response 500: kegagalan koneksi blockchain/DB
```

```
METHOD   : GET
URL      : /api/v1/records
Auth     : Bearer token + role "publisher"

Response 200: array record registry milik issuer yang login (untuk dashboard)
```

## Business Logic

```
RULE-1: File kosong ditolak sebelum hashing (documents.py:29-30).
RULE-2: File > MAX_UPLOAD_BYTES ditolak sebelum hashing (documents.py:31-32).
RULE-3: Jika PITS_ISSUER_ID tidak diset di env, fallback ke DEFAULT_ISSUER_ID hardcoded
        (register/route atau documents.py) — RISIKO: silent misconfiguration, lihat
        _docs/audit/audit-2026-07-31.md #8 (sisi frontend) dan pastikan backend juga
        tidak diam-diam pakai issuer salah di produksi.
RULE-4: Hash dihitung dari byte file mentah (bukan dari representasi lain) — SHA-256 standar.
```

## Permission & Access Control

| Role | Register Dokumen | Lihat Records | Verifikasi |
|------|-----|-----|-----|
| Publisher | ✅ | ✅ (hanya milik sendiri) | ✅ (publik, tidak perlu role) |
| Publik (tanpa login) | ❌ | ❌ | ✅ |

## Validation Rules

| Field | Aturan | Layer | Pesan Error |
|-------|--------|-------|--------------|
| file | wajib ada, tidak kosong | server (`documents.py`) | 400 file kosong |
| file | ukuran ≤ `MAX_UPLOAD_BYTES` | server | 400/413 file terlalu besar |
| Authorization header | JWT valid, role `publisher` | server (`keycloak.py`) | 401/403 |

## Testing Plan

```
□ Register dengan file valid + role publisher → 200, record tersimpan, tx hash valid
□ Register tanpa token / role salah → 401/403
□ Register file kosong → 400
□ Register file melebihi batas ukuran → 400/413
□ Kegagalan koneksi blockchain (Anvil down) → 500 dengan pesan jelas, tidak setengah tersimpan
□ GET /records hanya menampilkan record milik issuer yang login
```
*(Belum ada test integrasi otomatis untuk skenario di atas — lihat `_docs/audit/audit-2026-07-31.md` #4. Ini backlog prioritas tinggi.)*

---

## File yang HARUS Diubah (kalau fitur ini diubah)

| File | Perubahan | Kenapa |
|------|-----------|--------|
| `src/trustmark/api/v1/documents.py` | Endpoint register/records | Business logic utama |
| `src/trustmark/infra/hash_engine.py` | Algoritma hashing | Jika ganti algoritma hash |
| `src/trustmark/infra/blockchain_connector.py` | Logic anchoring | Jika ganti chain/strategi anchoring |
| `src/trustmark/registry/models.py` | Skema record | Jika field registry berubah |

---

## Security Considerations

- [x] Endpoint memerlukan auth + role yang sesuai
- [x] Ukuran file dibatasi
- [ ] Validasi tipe MIME file diupload — **belum ada**, hanya validasi ukuran/kosong (lihat audit #2 sisi frontend, backend juga tidak validasi tipe)
- [x] Isi dokumen tidak disimpan/ter-log

## Rollout Plan

- [x] Sudah live di rilis UAT
- [ ] Migration DB — belum ada (disclosed limitation PoC)
- [ ] Monitoring dashboard produksi — belum ada, masih andalkan `/health` manual

## Open Questions

| Pertanyaan | Siapa yang harus menjawab | Deadline |
|------------|-------------------------------|----------|
| Apakah issuer ID akan multi-tenant di tahap berikutnya? | Product Owner | Sebelum keluar dari tahap PoC |
