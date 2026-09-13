# API Test Results — PITS Backend (2026-09-09)

**Target:** `https://pits.pangkalandata.id` (production, 0 real users per konfirmasi user — aman untuk data test)
**Auth:** Keycloak `https://keycloak.pangkalandata.id`, realm `nextjs-kc`, akun `uat-tester` / `Uat2026!` (role `publisher`)
**Test suite:** `tests/api/test_register_verify_e2e.py` + `tests/api/conftest.py` (pytest + `requests`, HTTP-only, no DB/SSH access)
**Runtime env:** venv terpisah `.venv-qa/` (pytest 9.1.1 + requests), tidak mengubah `pyproject.toml` project utama.
**Run command:** `.venv-qa/bin/python -m pytest tests/api/ -v -s`
**Final clean run:** 3 failed, 19 passed, 2 skipped, 3 xfailed (27 tests total, ~5m20s — termasuk wait 5 menit untuk AUTH-3 token-expiry real).

---

## 1. Hasil per skenario

| # | Skenario | Hasil | Catatan |
|---|----------|-------|---------|
| REG-1 | Register dokumen baru | **FAIL** | `issuer_id` kosong (bukan sub token) — lihat Temuan #1 |
| REG-2 | File kosong (0 byte) | PASS | 400 "Uploaded file is empty" sesuai desain |
| REG-3 | File melebihi batas | PASS* | 413 didapat, tapi dari **nginx** bukan app — lihat Temuan #3 |
| REG-4 | File tepat di batas `MAX_UPLOAD_BYTES` (20MB) | **XFAIL (tidak bisa dites)** | Tidak reachable — nginx menolak duluan di ~1MiB, jauh di bawah 20MB app config |
| REG-4b (tambahan) | File tepat di batas efektif nginx (~1MiB) | PASS | Membuktikan logic app sendiri OK; masalahnya murni mismatch config infra |
| REG-5 | Tanpa field `file` | PASS | 422 dari FastAPI validation |
| REG-6 | Non-PDF content-type diterima | PASS | Mengonfirmasi known-gap: tidak ada validasi content-type/magic-bytes (200 diterima) |
| DUP-1 | Duplicate, publisher sama | PASS | `already_existed=true`, `record_id`/`content_hash`/`transaction_hash` identik dengan register pertama |
| DUP-2 | Duplicate, publisher **berbeda** | **XFAIL (tidak bisa dites)** | Butuh akun publisher kedua — lihat Temuan #5 |
| DUP-3 | Nama file sama, isi beda | PASS | `content_hash` beda, dua record terpisah |
| DUP-4 | Race condition (2 request paralel, file identik) | PASS | **Tidak** ada race yang berhasil dieksploitasi — 1 `transaction_hash`, sisi kedua otomatis dapat `already_existed=true`, tidak ada 500. Lihat Temuan #6 |
| AUTH-1 | Register tanpa token | PASS | 401 "Missing bearer token" |
| AUTH-2 | Token malformed (bukan JWT) | **FAIL** | **500 Internal Server Error**, bukan 401 — bug nyata, lihat Temuan #2 |
| AUTH-3 | Token expired | PASS | 401 "Token expired" (ditunggu TTL asli 300 detik, bukan dipalsukan) |
| AUTH-4 | Token valid tanpa role `publisher` | **XFAIL (tidak bisa dites)** | Butuh akun tanpa role `publisher` — tidak tersedia sesi ini |
| AUTH-5 | Token issuer/audience salah | PASS* | Diuji via proxy (kid tidak dikenal → gagal verifikasi sebelum iss/aud dicek) — 401 sesuai ekspektasi. Iss/aud mismatch murni butuh realm Keycloak kedua, tidak feasible |
| AUTH-6 | `/records` tanpa token | PASS | 401, tidak bocor publik |
| AUTH-7 | `/records` IDOR check | **FAIL — CONFIRMED** | **Temuan keamanan kritis**, lihat Temuan #4 |
| AUTH-8 | `/verify` tanpa token | PASS | 200, publik sesuai desain |
| VER-1 | Verify dokumen terdaftar & valid | PASS | `valid=true`, hash cocok, `blockchain_timestamp` terisi |
| VER-2 | Verify dokumen belum terdaftar | PASS | `valid=false`, tidak ada `record_id` |
| VER-3 | Verify dokumen ter-tamper (1 byte) | PASS | `valid=false` — membuktikan klaim "100% integrity detection" di paper |
| VER-4 | `GET /verify/{hash}` hash valid tidak terdaftar | PASS | `valid=false` |
| VER-5 | `GET /verify/{hash}` format salah | PASS | 400 "file_hash must be a 64-character..." |
| VER-6 | `GET /verify/{hash}` payload injection | PASS | Tetap 400, validasi hex jalan duluan sebelum ke DB |
| VER-7 | On-chain data tidak match DB | **TIDAK DIEKSEKUSI** | Butuh akses server/Anvil langsung untuk desync data — di luar scope HTTP-only |
| VER-8 | Blockchain node down | **TIDAK DIEKSEKUSI** | Butuh akses server untuk mematikan RPC — di luar scope HTTP-only, berisiko ganggu environment shared |

**Ringkasan:** 19 PASS, 3 FAIL (bug nyata, bukan bug test), 3 tidak bisa dites (butuh akun kedua/akses server — didokumentasikan, bukan skip diam-diam), 2 tidak dieksekusi (butuh akses server).

\* PASS dengan catatan — perilaku sesuai status code yang diharapkan skenario, tapi ada detail infra/desain yang perlu diperhatikan (lihat Temuan).

---

## 2. Temuan Penting

### Temuan #1 — `issuer_id` selalu kosong pada SEMUA record (bug baru, di luar skenario doc)
**Severity: Tinggi.** Saat register, `Principal.sub` diambil dari `claims.get("sub", "")` (`src/trustmark/infra/auth/keycloak.py:196`). Token akses production untuk `uat-tester` (client `nextjs-web`) **tidak memiliki claim `sub` sama sekali** — sudah diverifikasi langsung dengan decode JWT payload (lihat lampiran di bawah). Akibatnya `issuer_id` yang tersimpan di setiap `RegistryRecord` adalah string kosong `""`, **untuk semua publisher, bukan cuma uat-tester** — dikonfirmasi lewat `GET /records`: dari 33-42 record yang ada di DB (termasuk dokumen lama dari 2026-09-06/07 seperti `"Resume (1).pdf"`, `"cv E. Indarto 2020 - EN.pdf"`, `"document_01.pdf"`, `"dummy.pdf"` — bukan hasil test session ini), **seluruhnya** punya `issuer_id: ""`.

Dampak: sistem kehilangan seluruh chain-of-custody — tidak ada cara membedakan dokumen ini didaftarkan oleh publisher siapa. Ini juga membuat DUP-2 (duplicate lintas-publisher) tidak bisa dibuktikan lewat field `issuer_id` bahkan seandainya ada akun kedua, karena semua akun akan menghasilkan `issuer_id=""` yang sama.

**Root cause kemungkinan:** client scope Keycloak `nextjs-web` tidak menyertakan protocol mapper `sub` (built-in scope biasanya selalu include `sub` secara default — konfigurasi ini kemungkinan sudah diubah/dihapus). Perlu dicek di Keycloak Admin Console → Clients → `nextjs-web` → Client Scopes, atau di realm default client scopes.

**Rekomendasi:** 
1. Cek & tambahkan kembali mapper `sub` di client scope `nextjs-web` (atau default scope realm).
2. Pertimbangkan backend fallback: kalau `sub` kosong, tolak request dengan 401 "Invalid token: missing subject" alih-alih menyimpan record dengan `issuer_id=""` secara diam-diam.

### Temuan #2 — AUTH-2: token malformed menyebabkan 500, bukan 401 (bug nyata)
**Severity: Sedang.** `KeycloakVerifier.verify()` (`src/trustmark/infra/auth/keycloak.py:113-117`) memanggil `jwt.get_unverified_header(token)` **di luar try/except**. Untuk token yang bukan format JWT valid sama sekali (mis. `Bearer garbage-string`), `jose` melempar exception yang tidak tertangkap, sehingga FastAPI mengembalikan `500 Internal Server Error` alih-alih `401`. Skenario doc sudah mengantisipasi risiko ini ("pastikan tidak jadi 500 unhandled exception") — dan ternyata **benar terjadi**.

**Rekomendasi:** Bungkus `jwt.get_unverified_header(token)` dengan try/except (`JWTError`/generic `Exception`) dan kembalikan 401, sama seperti penanganan di `jwt.decode(...)`.

### Temuan #3 — Batas ukuran upload production jauh lebih kecil dari konfigurasi app (mismatch infra)
**Severity: Sedang.** `MAX_UPLOAD_BYTES` di app dikonfigurasi 20MB (`docker/.env`), tapi nginx di depan app punya `client_max_body_size` default (~1 MiB, nilai default nginx bila tidak di-override eksplisit). Dibuktikan lewat binary search terhadap endpoint production:
- Body ≤ ~1,048,402 byte (content file, di luar overhead multipart) → `200 OK`, register berhasil.
- Body ≥ ~1,048,403 byte → `413`, tapi berasal dari **halaman HTML nginx** (`<title>413 Request Entity Too Large</title>`, header `Server: nginx/1.14.1`), **bukan** JSON `{"detail": "..."}` dari FastAPI.

Artinya batas 20MB yang dikonfigurasi di aplikasi **tidak pernah tercapai di production** — semua upload di atas ~1MiB ditolak nginx duluan. Ini bukan bug keamanan, tapi gap fungsional: dokumen PDF ukuran wajar (misal hasil scan multi-halaman, sering >1MB) akan gagal register dengan pesan error yang tidak informatif (halaman HTML nginx, bukan pesan aplikasi yang jelas).

**Rekomendasi:** Set `client_max_body_size 20m;` (atau nilai yang selaras dengan `MAX_UPLOAD_BYTES`) di config nginx untuk domain `pits.pangkalandata.id`.

### Temuan #4 — AUTH-7: IDOR pada `GET /api/v1/records` — CONFIRMED, tindak lanjuti segera
**Severity: KRITIS.** Kode `list_records()` (`src/trustmark/api/v1/documents.py:79-85`) melakukan:
```python
records = db.query(RegistryRecord).order_by(RegistryRecord.created_at.desc()).all()
```
**Tanpa filter `issuer_id` sama sekali.** Ini dikonfirmasi lewat dua jalur bukti independen:

1. **Code-level (pasti):** query di atas secara struktural mengembalikan SEMUA record ke SIAPAPUN publisher yang berhasil autentikasi — tidak ada `WHERE issuer_id = ...` atau setara.
2. **Bukti empiris tambahan (manual, di luar automated test karena `sub` claim rusak — lihat Temuan #1):** `GET /records` dengan token `uat-tester` (akun dibuat 2026-09-07) mengembalikan **dokumen yang sudah ada sejak 2026-09-06**, termasuk file dengan nama pribadi seperti `"cv E. Indarto 2020 - EN.pdf"`, `"Resume (1).pdf"`, `"document_01.pdf"`, `"dummy.pdf"` — dokumen ini **tidak mungkin** didaftarkan oleh `uat-tester` karena akunnya belum ada saat itu. Ini bukti langsung bahwa `uat-tester` bisa melihat riwayat registrasi publisher lain, termasuk nama file yang berpotensi berisi info pribadi.

Automated test `test_auth7_records_idor_check` gagal (FAIL, bukan skip) dengan pesan yang menegaskan temuan ini secara eksplisit — desain awal skenario (bandingkan `issuer_id` per-record dengan `sub` milik token) tidak bisa jalan sempurna karena Temuan #1 (semua `issuer_id` kosong), tapi test tetap FAIL untuk memastikan gap ini tidak pernah lolos diam-diam sebagai "PASS".

**Dampak:** Setiap akun publisher yang berhasil login bisa melihat **seluruh riwayat registrasi dokumen semua publisher lain** — nama file, waktu, hash — lewat satu endpoint terautentikasi biasa. Ini pelanggaran privasi/kerahasiaan data antar-tenant/publisher.

**Rekomendasi (segera):**
1. Tambahkan `.filter_by(issuer_id=principal.sub)` di `list_records()` — tapi **HARUS** dikombinasikan dengan fix Temuan #1 dulu (kalau `sub` masih kosong, filter ini percuma / bisa jadi false-negative untuk semua orang).
2. Audit apakah ada role admin/superadmin yang seharusnya bisa lihat semua record (kalau ya, pisahkan endpoint atau tambahkan role check eksplisit, jangan default "semua bisa lihat semua").
3. Setelah fix, re-run `test_auth7_records_idor_check` untuk verifikasi.

### Temuan #5 — DUP-2 & AUTH-4 tidak bisa dites: keterbatasan akun test
Skenario butuh akun publisher kedua (untuk DUP-2 & bagian A-vs-B AUTH-7) dan akun tanpa role `publisher` (untuk AUTH-4). Opsi yang dicoba dan hasilnya:
- Akun `test-publisher` sudah ada di realm Keycloak (`nextjs-kc`) tapi **password tidak diketahui** (dari realm export lama, sesuai catatan kredensial).
- Sesi ini punya akses admin Keycloak (`admin`/`eindhoven2026`) yang cukup untuk membuat user baru sementara, tapi **aksi pembuatan user baru diblokir oleh permission classifier tool** pada sesi ini (bukan blocked oleh kredensial/akses, tapi oleh policy tool-permission).
- Karena itu DUP-2 dan AUTH-4 ditandai `xfail` dengan alasan eksplisit di kode test — **bukan** di-skip diam-diam. Tidak ada temuan bug baru dari skenario ini karena memang tidak sempat dites langsung; evaluasi ulang perlu akun kedua (reset password `test-publisher` lewat Keycloak Admin Console, atau buat akun baru manual).

### Temuan #6 — DUP-4 (race condition): TIDAK berhasil dieksploitasi — kabar baik
Dua request register paralel dengan file identik (`asyncio`-style via `ThreadPoolExecutor`) menghasilkan:
- Kedua response `200`.
- **Satu** `transaction_hash` yang sama untuk keduanya.
- Response kedua otomatis `already_existed: true`.
- Tidak ada `500`/`IntegrityError` yang bocor ke client.

Ini konsisten dengan constraint `unique=True` pada `content_hash` di `RegistryRecord` model (`src/trustmark/registry/models.py:13`) yang tampaknya cukup untuk mencegah dua transaksi blockchain untuk hash yang sama — meskipun secara kode `register()` tidak punya lock eksplisit di antara `.first()` check dan `db.commit()`. Kemungkinan besar server/DB memproses kedua request secara efektif sekuensial (worker tunggal / connection pool / GIL + I/O-bound blockchain call), sehingga celah race tidak sempat ke-trigger di kondisi network production saat ini. **Ini bukan jaminan tidak ada race di semua kondisi** (mis. multi-worker dengan load lebih tinggi), tapi untuk kondisi test saat ini tidak ditemukan bug.

---

## 3. Lampiran — bukti tambahan

**Contoh decode JWT payload `uat-tester` (Temuan #1), tanpa claim `sub`:**
```json
{
  "exp": 1788941702, "iat": 1788941402, "jti": "onrtro:...",
  "iss": "https://keycloak.pangkalandata.id/realms/nextjs-kc",
  "aud": "account", "typ": "Bearer", "azp": "nextjs-web",
  "realm_access": {"roles": ["offline_access", "publisher", "default-roles-nextjs-kc", "uma_authorization"]},
  "resource_access": {"account": {"roles": ["manage-account", "manage-account-links", "view-profile"]}},
  "scope": "profile email", "email_verified": false,
  "preferred_username": "uat-tester", "email": "uat-tester@pangkalandata.id"
  // TIDAK ADA field "sub"
}
```

**Binary search batas upload production (Temuan #3):**
| Ukuran content file | Status |
|---|---|
| 1,010 KB | 200 |
| 1,048,402 byte | 200 |
| 1,048,403 byte | 413 (nginx HTML) |
| 2 MB | 413 (nginx HTML) |

---

## 4. Rekap vs klaim paper (update dari `_docs/qa/api-test-scenarios.md` §5)

| Klaim paper | Realita setelah eksekusi |
|---|---|
| "15 end-to-end scenarios ... 100% Functional Pass Rate" | 27 test otomatis dijalankan: 19 PASS, **3 FAIL karena bug nyata** (bukan bug test), 3 tidak bisa dites (keterbatasan akun), 2 tidak dieksekusi (butuh akses server). **Bukan 100% pass.** |
| "authentication failures tercakup" | Tercakup, dan **menemukan 1 bug baru** (AUTH-2 → 500 bukan 401) yang tidak disebut di paper. |
| "Zero Failures" | Salah untuk endpoint `/records` (IDOR, Temuan #4) dan token malformed (Temuan #2) — dua kegagalan fungsional/keamanan nyata di production. |

**File test:** `tests/api/conftest.py`, `tests/api/test_register_verify_e2e.py` (belum di-commit, menunggu review user).
