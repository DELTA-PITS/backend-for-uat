# API Test Scenarios — PITS Backend (Register / Verify / Duplicate / Auth)

**Status:** Draft skenario, belum diimplementasi sebagai test otomatis
**Tanggal:** 2026-09-09
**Konteks:** Repo ini (`backend-for-uat`) saat ini hanya punya 15 unit test infra (`tests/trustmark/infra/test_hash_engine.py` — 7, `tests/trustmark/infra/test_blockchain_connector.py` — 8). **Tidak ada test otomatis untuk endpoint API** (`/api/v1/register`, `/api/v1/verify`, `/api/v1/verify/{file_hash}`, `/api/v1/records`) meskipun paper akademik (Khairunnisa et al., "From Hoax to Hash") mengklaim "15 end-to-end scenarios (registration, duplicate detection, authentication failures, verification)". Dokumen ini menutup gap tersebut dengan skenario nyata berbasis kode di `src/trustmark/api/v1/documents.py` dan `src/trustmark/infra/auth/keycloak.py`.
**Target eksekusi:** Server UAT/production (`https://pits.pangkalandata.id`), akun test `uat-tester`/`Uat2026!` (role `publisher`) — lihat kredensial di memory `reference_project_trustmark_pits_credentials`.
**Prinsip:** Setiap skenario harus punya expected status code + expected response shape yang bisa diverifikasi otomatis (pytest + `httpx`), bukan cuma "cek manual berhasil/gagal".

---

## 1. Registration — happy path & validation

| # | Skenario | Request | Expected |
|---|----------|---------|----------|
| REG-1 | Register dokumen baru (belum pernah didaftarkan) | `POST /api/v1/register`, Bearer token publisher valid, file PDF unik | `200`, `stored=true`, `already_existed=false`, `content_hash` = SHA-256 file, `transaction_hash` terisi (non-empty), `issuer_id` = `sub` dari token |
| REG-2 | File kosong (0 byte) | `POST /api/v1/register`, file 0 byte | `400`, detail "Uploaded file is empty" |
| REG-3 | File melebihi batas ukuran | File > `MAX_UPLOAD_BYTES` (cek nilai aktual di `conf/settings.toml` / env server, default kode 20MB) | `413`, detail "Uploaded file exceeds the configured size limit" |
| REG-4 | File tepat di batas ukuran (boundary) | File = `MAX_UPLOAD_BYTES` byte persis | `200` (bukan `413` — cek operator `>` bukan `>=` di `_read_upload`) |
| REG-5 | Tanpa field `file` di multipart | `POST /api/v1/register` tanpa file | `422` (FastAPI validation error, bukan `400`/`500`) |
| REG-6 | Content-Type file bukan PDF (mis. `.exe` diganti ekstensi `.pdf`, atau file `.txt` asli) | Upload file non-PDF | **Cek known-gap**: endpoint saat ini tidak validasi `content_type`/magic-bytes sama sekali — tulis skenario ini untuk *mendokumentasikan* bahwa non-PDF **diterima** (200), lalu putuskan apakah ini bug atau by-design (server percaya frontend sudah filter). Jangan asumsikan ini otomatis harus ditolak. |

## 2. Duplicate detection

| # | Skenario | Request | Expected |
|---|----------|---------|----------|
| DUP-1 | Register dokumen yang sudah ada (byte identik) | Register file A → register file A lagi (publisher sama) | Response kedua: `200`, `already_existed=true`, `record_id`/`content_hash`/`transaction_hash` **sama persis** dengan response pertama. **Tidak** ada transaksi blockchain baru dibuat (cek `transaction_hash` identik, bukan cuma format valid). |
| DUP-2 | Register dokumen sama tapi publisher **berbeda** (dua akun publisher berbeda upload byte identik) | Publisher A register file X → Publisher B register file X | `200`, `already_existed=true`, `issuer_id` di response = issuer **pertama** (Publisher A) — dokumentasikan bahwa sistem tidak mencatat multi-issuer untuk hash yang sama. Ini penting untuk paper: model data saat ini 1 hash → 1 issuer, "duplicate" milik publisher lain tidak membuat record baru. |
| DUP-3 | Dua file berbeda isi tapi nama file sama | Upload file A (isi X) lalu file B (isi Y), nama file sama | Dianggap dokumen berbeda (`content_hash` beda), keduanya tercatat sebagai record terpisah — pastikan `already_existed=false` untuk keduanya. |
| DUP-4 (race) | Dua request register file identik dikirim **bersamaan** (concurrent, race condition) | Kirim 2 request paralel (async) dengan file byte sama, publisher sama | **Adversarial**: cek apakah bisa terjadi 2 transaksi blockchain dibuat untuk hash yang sama akibat race antara `db.query(...).first()` check dan `db.commit()` (tidak ada unique constraint/lock terlihat di kode `register()`). Kalau race berhasil menghasilkan 2 record dengan `content_hash` sama → **bug nyata**, catat sebagai temuan, bukan expected behavior. |

## 3. Authentication & authorization failures

| # | Skenario | Request | Expected |
|---|----------|---------|----------|
| AUTH-1 | Register tanpa header `Authorization` | `POST /api/v1/register`, no Bearer token | `401`, detail "Missing bearer token" |
| AUTH-2 | Register dengan token malformed (bukan JWT valid) | `Authorization: Bearer garbage-string` | `401` (dari `jwt.get_unverified_header` gagal parse — pastikan tidak jadi `500` unhandled exception) |
| AUTH-3 | Register dengan token expired | Token JWT valid tapi `exp` sudah lewat (bisa pakai token lama dari sesi sebelumnya, atau generate via Keycloak dengan TTL pendek) | `401`, detail "Token expired" |
| AUTH-4 | Register dengan token valid tapi role `publisher` **tidak ada** (mis. akun verifier-only atau realm role lain) | Token valid, `roles` tidak include `publisher` | `403`, detail "Forbidden" |
| AUTH-5 | Register dengan token dari **issuer lain** (bukan `nextjs-kc`) atau audience salah | Token JWT valid tapi `iss`/`aud` claim tidak cocok config server | `401` (verify_iss/verify_aud gagal → `JWTError` → "Invalid token") |
| AUTH-6 | `GET /api/v1/records` tanpa token | Tidak ada Bearer token | `401` — pastikan endpoint list records **tidak** bocor ke publik (ini endpoint sensitif berisi riwayat semua dokumen) |
| AUTH-7 | `GET /api/v1/records` dengan token publisher A — apakah publisher A bisa lihat record publisher B? | Login publisher A, panggil `/records` | **Adversarial/IDOR check**: baca kode `list_records()` — query-nya `db.query(RegistryRecord).order_by(...).all()` **tanpa filter `issuer_id`**. Ini berarti **semua publisher melihat semua record dari semua publisher**, bukan cuma miliknya sendiri. Verifikasi ini benar-benar terjadi di server (bikin 2 akun publisher, masing-masing register 1 dokumen, cek apakah `/records` publisher A menampilkan dokumen publisher B). Kalau iya → **catat sebagai temuan keamanan/privasi**, bukan asumsikan sudah difilter. |
| AUTH-8 | `POST /api/v1/verify` **tanpa** token | Tidak ada Bearer token, upload file | `200` — endpoint ini memang publik by design (sesuai BRD "verifikasi tanpa login"), pastikan tetap `200` bukan `401` (regression check kalau ada perubahan middleware global) |

## 4. Verification

| # | Skenario | Request | Expected |
|---|----------|---------|----------|
| VER-1 | Verifikasi dokumen yang sudah terdaftar & valid | `POST /api/v1/verify`, file yang sama persis dengan yang diregister | `200`, `valid=true`, `content_hash` cocok, `blockchain_timestamp` terisi |
| VER-2 | Verifikasi dokumen yang belum pernah didaftarkan | File random baru | `200`, `valid=false`, `content_hash` terisi (bukan `record_id`) |
| VER-3 | Verifikasi dokumen yang byte-nya dimodifikasi setelah register (1 byte diubah) | Register file A → ubah 1 byte → verify file A' | `200`, `valid=false` (karena `content_hash` beda total dari SHA-256 avalanche effect) — ini skenario "integrity detection" yang diklaim paper 100% akurat, tes ini yang membuktikannya |
| VER-4 | `GET /api/v1/verify/{file_hash}` dengan hash valid 64-char hex tapi tidak terdaftar | Hash SHA-256 valid format, tidak ada di DB | `200`, `valid=false` |
| VER-5 | `GET /api/v1/verify/{file_hash}` dengan hash format salah (panjang ≠ 64, atau ada karakter non-hex) | `file_hash=abc123` (terlalu pendek) atau ada huruf `g`/`z` | `400`, detail "file_hash must be a 64-character SHA-256 hexadecimal value" |
| VER-6 | `GET /api/v1/verify/{file_hash}` dengan SQL-injection-like payload di path param | `file_hash="' OR '1'='1"` atau `../../etc/passwd` | **Adversarial**: harus tetap `400` (gagal validasi format hex sebelum sampai ke query DB — cek kode validasi jalan duluan, bukan langsung masuk `filter_by`) |
| VER-7 | Verify dokumen yang record-nya ada di DB tapi **tx hash on-chain tidak match** (data tercatat di Postgres tapi entry blockchain dimanipulasi/hilang) | Perlu setup khusus (mis. edit `content_hash` di DB Anvil-side atau pakai record lama dengan Anvil yang sudah di-restart sehingga chain state hilang) | `200`, `valid=false`, tapi **`record_id` tetap muncul** di response (lihat kode: `{"valid": False, "content_hash": ..., "record_id": existing.id}`) — ini beda dari VER-2 yang tidak ada `record_id` sama sekali. Penting untuk UI membedakan "tidak pernah didaftarkan" vs "terdaftar tapi bukti blockchain tidak cocok". |
| VER-8 | Verify saat Anvil/blockchain node down | Matikan koneksi ke `BLOCKCHAIN_RPC_URL` sementara (atau simulasikan) lalu verify dokumen yang sudah terdaftar | Cek apakah error di `read_transaction_value` ter-handle jadi response terkontrol (mis. `503`) atau malah `500` unhandled — **jangan asumsikan sudah ada error handling**, `verify_full`/`verify_by_hash` tidak punya try/except di sekitar `blockchain_service.read_transaction_value(...)`. |

---

## 5. Ringkasan gap vs klaim paper

| Klaim paper | Realita kode saat ini |
|---|---|
| "15 end-to-end scenarios" | 15 **unit test infra** (hash engine + blockchain connector mock), bukan E2E API |
| "registration, duplicate detection, authentication failures, verification" tercakup | 0 automated test untuk 4 area ini di level API |
| "100% Functional Pass Rate" | Belum ada test suite functional yang bisa dijalankan untuk membuktikan angka ini |
| "Zero Failures" / Locust load test | Locust files ada (`tests/locust/`) tapi hasil run tidak tersimpan di repo — perlu re-run untuk verifikasi angka 2665 request/44.96 rps di paper |

**Rekomendasi:** Sebelum submit revisi paper (atau paper final), jalankan skenario di atas sebagai pytest nyata terhadap server UAT, simpan hasilnya (pass/fail + response snapshot) di `_docs/qa/results/`, supaya klaim di paper punya bukti yang bisa direproduksi — bukan cuma angka di teks.

---

## 6. Yang sengaja TIDAK dites di skenario ini (di luar scope PoC)

- Beban tinggi (>10 concurrent user) — sudah dicakup Locust config existing, tidak diduplikasi di sini.
- Penetrasi keamanan mendalam (fuzzing JWT signature, replay attack penuh) — kandidat sesi security-review terpisah kalau AUTH-7 (IDOR `/records`) terbukti nyata dan perlu ditindaklanjuti lebih dalam.
- Recovery/disaster testing Postgres atau Anvil (restore dari backup) — di luar scope functional/integrity testing paper.
