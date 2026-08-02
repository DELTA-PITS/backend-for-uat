# BRD — PITS Backend Core (Registrasi & Verifikasi Dokumen)

**Status:** Approved (retroaktif — mendokumentasikan sistem yang sudah dibangun untuk UAT)
**Author:** Tim DELTA-PITS (didokumentasikan ulang oleh Claude Code)
**Tanggal:** 2026-07-31
**Project:** backend-for-uat (trustmark)

---

## 1. Latar Belakang

Dokumen digital (pengumuman resmi, laporan, rilis publik, dsb) mudah diedit, disalin, atau disebarkan ulang tanpa cara mudah bagi pembaca untuk memverifikasi apakah isi dokumen tersebut masih sama persis dengan versi asli yang diterbitkan penerbit (publisher). Tidak ada mekanisme sederhana dan dapat diverifikasi publik untuk membuktikan "dokumen ini persis seperti yang saya terbitkan tanggal X, belum diubah."

PITS dibangun sebagai proof-of-concept untuk menjawab kebutuhan ini: penerbit mendaftarkan hash dokumen ke registry yang di-anchor ke blockchain (bukti tidak bisa diubah/immutable), dan siapa pun dapat memverifikasi keaslian dokumen secara publik tanpa perlu akun atau kredensial.

## 2. Masalah

- Penerbit tidak punya cara murah dan cepat untuk membuktikan "dokumen versi final saya adalah yang ini" secara publik dan dapat diverifikasi independen.
- Pembaca/verifikator tidak punya cara mengecek apakah dokumen yang mereka pegang identik dengan versi resmi, tanpa harus menghubungi penerbit secara manual.
- Solusi existing (tanda tangan digital biasa, watermark, PDF metadata) mudah dipalsukan/dihapus dan tidak punya bukti pihak ketiga yang independen dari verifikasi.

## 3. User Persona

| Persona | Role | Familiaritas Teknis | Goals | Pain Points | Frekuensi Pakai |
|---------|------|----------------------|-------|-------------|-------------------|
| Publisher | Penerbit dokumen resmi (institusi/organisasi) | Menengah — non-developer tapi terbiasa pakai portal web | Mendaftarkan dokumen resmi agar keasliannya bisa diverifikasi publik | Tidak ada bukti keaslian yang bisa diverifikasi independen; takut dokumen dipalsukan/diedit lalu disebar ulang | Setiap kali menerbitkan dokumen resmi baru |
| Verifier (publik) | Siapa pun yang menerima dokumen dan ingin cek keasliannya | Awam — masyarakat umum | Memastikan dokumen yang diterima identik dengan versi resmi penerbit | Tidak tahu cara mengecek keaslian dokumen, tidak mau ribet daftar akun | Sesekali, saat ragu terhadap keaslian dokumen tertentu |

## 4. User Journey

### Journey Publisher (Registrasi)
```
ENTRY POINT   : Login via Keycloak (role publisher) di frontend portal
LANGKAH 1     : Upload dokumen yang akan didaftarkan
LANGKAH 2     : Backend hitung SHA-256 dari byte dokumen (di memory, tidak disimpan)
LANGKAH 3     : Backend buat transaksi anchoring hash ke blockchain (Anvil lokal)
LANGKAH 4     : Backend simpan record registry (hash, tx hash, metadata, timestamp)
LANGKAH 5     : Publisher lihat daftar dokumen terdaftar di dashboard (GET /records)
EXIT          : Dokumen terdaftar, publisher punya bukti tx blockchain sebagai referensi
```

### Journey Verifier (Verifikasi)
```
ENTRY POINT   : Buka halaman verifikasi publik (tanpa login)
LANGKAH 1     : Upload dokumen yang ingin dicek, ATAU masukkan hash SHA-256 langsung
LANGKAH 2     : Backend hitung/terima hash, cari di registry
LANGKAH 3     : Backend cocokkan dengan record on-chain
EXIT          : Verifier melihat hasil: "Terverifikasi — terdaftar oleh [issuer] pada [tanggal]" atau "Tidak ditemukan/tidak cocok"
```

## 5. Tujuan & Success Metric

| Tujuan | Metric | Target |
|--------|--------|--------|
| Publisher bisa mendaftarkan dokumen dengan bukti anchoring blockchain | Waktu dari upload sampai record tersimpan + tx hash tersedia | < 5 detik (lingkungan Anvil lokal) |
| Verifier bisa memverifikasi dokumen tanpa akun | Endpoint verify dapat diakses tanpa auth, response < 2 detik | 100% request verify tanpa auth berhasil diproses |
| Sistem tidak menyimpan isi dokumen mentah | Audit kode: tidak ada penyimpanan file lengkap di storage/DB | 0 temuan penyimpanan file mentah |

## 6. Scope

### In Scope
- Registrasi dokumen oleh publisher terautentikasi (role `publisher`) via Keycloak.
- Hashing SHA-256 dokumen, anchoring ke blockchain lokal (Anvil).
- Verifikasi publik via upload dokumen atau input hash langsung.
- Listing record registry untuk dashboard publisher.

### Out of Scope
- Verifikasi kebenaran **isi** dokumen (PITS hanya membuktikan integritas byte, bukan validitas konten/fakta — lihat README § limitasi).
- Penyimpanan dokumen lengkap/arsip dokumen.
- Multi-tenant issuer management (saat ini issuer ID sebagian besar single-tenant/default).
- Migrasi skema database — PoC ini mengasumsikan volume Postgres baru setiap deployment ulang.
- Blockchain publik/mainnet — hanya Anvil lokal untuk PoC.

## 7. User Stories

```
SEBAGAI Publisher,
SAYA INGIN mendaftarkan hash dokumen resmi saya ke sistem yang ter-anchor ke blockchain,
SUPAYA saya punya bukti independen bahwa dokumen tersebut belum diubah sejak didaftarkan.

Acceptance Criteria:
- [x] Endpoint POST /api/v1/register menerima file upload, menghasilkan hash + tx hash blockchain
- [x] Endpoint hanya bisa diakses oleh user dengan role publisher (Keycloak JWT)
- [x] Ukuran file dibatasi (MAX_UPLOAD_BYTES), file kosong ditolak

SEBAGAI Verifier (publik),
SAYA INGIN mengecek apakah dokumen yang saya punya identik dengan versi terdaftar,
SUPAYA saya bisa memastikan keasliannya tanpa harus menghubungi penerbit.

Acceptance Criteria:
- [x] Endpoint POST /api/v1/verify menerima file upload tanpa autentikasi
- [x] Endpoint GET /api/v1/verify/{sha256} menerima hash langsung, validasi format hex 64 karakter
- [x] Hasil verifikasi menyatakan cocok/tidak cocok dengan record on-chain
```

## 8. Asumsi & Dependensi

| Asumsi | Risiko kalau asumsi salah |
|--------|------------------------------|
| Anvil lokal cukup untuk membuktikan konsep anchoring blockchain | Kalau perlu bukti tahan-lama (persist), harus pindah ke testnet/mainnet publik — perubahan arsitektur signifikan |
| Keycloak realm `nextjs-kc` sudah dikonfigurasi benar sebelum deployment | Registrasi publisher gagal total kalau realm/role salah konfigurasi |
| Frontend selalu mengirim file yang sudah tervalidasi dasar | Backend jadi satu-satunya lapis validasi upload — beban validasi penuh di backend |

| Dependensi | Status |
|------------|--------|
| Keycloak (realm + client secret + role `publisher`) | Tersedia (docker-compose lokal) |
| Anvil JSON-RPC lokal | Tersedia (docker-compose lokal) |
| PostgreSQL | Tersedia (docker-compose lokal), tanpa migration |

## 9. Risiko

| Risiko | Kemungkinan | Dampak | Mitigasi |
|--------|-------------|--------|----------|
| `TEST_MODE` bypass auth aktif tanpa sengaja di environment bukan-test | Sedang | Tinggi | Tambahkan guard eksplisit `ENVIRONMENT != production` sebelum override auth (lihat `_docs/audit/audit-2026-07-31.md` #2) |
| Kredensial/private key ter-commit ke git (riwayat `.env.save`) | Rendah (sudah terjadi sekali) | Tinggi | Bersihkan history, perbaiki pola `.gitignore` |
| Tidak ada test untuk endpoint register/verify dan modul auth | Tinggi | Tinggi | Tambahkan test integrasi sebelum lanjut dari tahap UAT |

## 10. Keputusan

| Keputusan | Pilihan yang Dipertimbangkan | Pilihan yang Dipilih | Alasan |
|-----------|-------------------------------|-------------------------|--------|
| Blockchain untuk anchoring | Testnet publik, Anvil lokal | Anvil lokal | PoC — biaya nol, deterministik, cukup untuk membuktikan konsep. Detail lihat ADR-001. |
| Auth provider | Auth custom, Keycloak | Keycloak | Standar OIDC, role-based access out of the box. Detail lihat ADR-002. |

## 11. Sign-off

| Peran | Nama | Status | Tanggal |
|-------|------|--------|---------|
| Product Owner | Tim DELTA-PITS | ☑ Approved (retroaktif, untuk rilis UAT) | 2026-07-31 |
