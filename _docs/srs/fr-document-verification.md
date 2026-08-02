# Design Doc — Verifikasi Dokumen Publik

**Status:** Implemented
**Author:** Tim DELTA-PITS (didokumentasikan ulang oleh Claude Code)
**Reviewer:** —
**Tanggal:** 2026-07-31
**Berkaitan dengan BRD:** `_docs/brd/brd-core.md`
**Definition of Ready:** ✅ terpenuhi (retroaktif)

---

## TL;DR

Siapa pun (tanpa login) bisa mengecek keaslian dokumen dengan upload file atau memasukkan hash SHA-256 langsung; backend mencocokkan dengan record registry dan mengembalikan status terverifikasi atau tidak.

---

## Context

Diimplementasikan di `src/trustmark/api/v1/documents.py` (endpoint `verify`). Tidak memerlukan autentikasi — desain sengaja publik agar siapa pun bisa memverifikasi tanpa akun.

---

## Goals & Non-Goals

**Goals:**
- Verifikasi publik tanpa autentikasi.
- Mendukung dua mode input: upload file atau hash langsung.

**Non-Goals:**
- Verifikasi kebenaran isi/konten dokumen — hanya integritas byte vs record terdaftar.

---

## Arsitektur & Data Flow

```
Publik → POST /api/v1/verify (upload file)
       → hash_engine.generate_hash(bytes)
       → cari record registry by hash
       → cocokkan dengan on-chain record (blockchain_connector)
       → Response: matched/not-found + metadata record (issuer, tanggal, tx hash)

Publik → GET /api/v1/verify/{sha256}
       → validasi format hex 64 karakter (whitelist char)
       → cari record registry by hash
       → Response sama seperti di atas
```

## Perubahan API / Interface

```
METHOD   : POST
URL      : /api/v1/verify
Auth     : none

Request  : multipart/form-data, field file

Response 200 (ditemukan):
{ "matched": true, "record_id": "...", "issuer_id": "...", "created_at": "...", "transaction_hash": "0x..." }

Response 200 (tidak ditemukan):
{ "matched": false }
```

```
METHOD   : GET
URL      : /api/v1/verify/{sha256}
Auth     : none

Path param sha256: 64 karakter hex, divalidasi ketat sebelum query DB (whitelist char, documents.py:112)

Response 400: format hash tidak valid
Response 200: sama seperti endpoint POST di atas
```

## Business Logic

```
RULE-1: Endpoint ini SELALU publik (tanpa auth) — jangan tambahkan requirement auth
        tanpa mendiskusikan dampaknya ke BRD (lihat CLAUDE.md § Constitution).
RULE-2: Hash path param divalidasi ketat (hanya hex, panjang tepat 64) sebelum
        dipakai di query DB — mencegah injection lewat parameter path.
RULE-3: Hasil verifikasi tidak membocorkan isi dokumen asli — hanya metadata
        registry (issuer, tanggal, tx hash).
```

## Permission & Access Control

| Role | Verifikasi via Upload | Verifikasi via Hash |
|------|-----|-----|
| Publik (tanpa login) | ✅ | ✅ |

## Validation Rules

| Field | Aturan | Layer | Pesan Error |
|-------|--------|-------|--------------|
| file (POST) | tidak kosong | server | 400 |
| sha256 (GET path param) | hex, tepat 64 karakter | server (`documents.py:112`) | 400 format tidak valid |

## Testing Plan

```
□ Verify dengan file yang cocok dengan record terdaftar → matched: true
□ Verify dengan file yang tidak terdaftar → matched: false
□ Verify dengan hash valid tapi tidak terdaftar → matched: false
□ Verify dengan hash format tidak valid (bukan hex/panjang salah) → 400
□ Verify tanpa auth header sama sekali → tetap 200 (bukan 401), pastikan tidak ada regresi yang menambahkan auth requirement
```
*(Belum ada test integrasi otomatis — lihat `_docs/audit/audit-2026-07-31.md` #4.)*

---

## File yang HARUS Diubah (kalau fitur ini diubah)

| File | Perubahan | Kenapa |
|------|-----------|--------|
| `src/trustmark/api/v1/documents.py` | Endpoint verify | Business logic utama |
| `src/trustmark/infra/hash_engine.py` | Algoritma hashing | Harus sama persis dengan yang dipakai saat register |

---

## Security Considerations

- [x] Endpoint publik by design — tidak perlu auth
- [x] Validasi format hash ketat sebelum query DB
- [x] Tidak ada isi dokumen asli yang dikembalikan di response

## Open Questions

| Pertanyaan | Siapa yang harus menjawab | Deadline |
|------------|-------------------------------|----------|
| Apakah perlu rate limiting untuk endpoint publik ini agar tidak disalahgunakan? | Product Owner / Security | Sebelum keluar dari tahap PoC |
