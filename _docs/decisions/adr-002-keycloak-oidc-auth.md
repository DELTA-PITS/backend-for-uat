# ADR-002 — Autentikasi & Role-Based Access via Keycloak (OIDC/JWT)

**Status:** Accepted
**Tanggal:** 2026-07-31 (didokumentasikan retroaktif)

## Konteks

Backend perlu membatasi endpoint `register` dan `records` hanya untuk publisher terverifikasi, sambil tetap membuka endpoint `verify` untuk publik tanpa akun. Dibutuhkan sistem identity & access management yang mendukung role-based access dan bisa dijalankan lokal untuk PoC/UAT tanpa dependency SaaS eksternal berbayar.

## Keputusan

Gunakan **Keycloak** sebagai identity provider (OIDC), dengan realm `nextjs-kc` yang di-export/import secara reproducible (`docker/realms/realm-export.json`). Backend memverifikasi JWT dari Keycloak via JWKS (fetch dari `.well-known/openid-configuration`, cache TTL 3600 detik) dan mengecek role `publisher` di klaim token.

## Alternatif yang Dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Keputusan |
|------|-----------|-----------|-----------|
| Auth custom (username/password + JWT self-issued) | Kontrol penuh, tanpa dependency eksternal | Harus implementasi ulang seluruh flow keamanan (hashing password, refresh token, dsb) — rawan bug keamanan buatan sendiri | ❌ Tidak dipilih |
| Auth0 / Firebase Auth (SaaS) | Setup cepat, managed | Biaya berlangganan, dependency ke layanan eksternal, tidak cocok untuk PoC yang harus jalan sepenuhnya lokal untuk demo offline | ❌ Tidak dipilih |
| Keycloak (self-hosted) — dipilih | Open source, standar OIDC, role-based access out of the box, bisa jalan sepenuhnya lokal via Docker, realm reproducible via export/import | Perlu maintenance container tambahan, konfigurasi realm/role manual | ✅ Dipilih |

## Konsekuensi

**Positif:** Verifikasi JWT standar (issuer, audience, expiry, algoritma RS256/PS256 di-hardcode — lihat `infra/auth/keycloak.py:134-142`), role-based access konsisten via `require_roles()`, realm reproducible untuk lingkungan test/demo baru.

**Negatif/Risiko:** Menambah satu container tambahan yang harus dijalankan dan dikonfigurasi (Keycloak + Postgres realm-nya sendiri). Client secret Keycloak (`pits-local-client-secret`) adalah nilai default lokal — harus diganti sebelum lingkungan apa pun yang bukan lokal/demo.

**Aturan turunan:** Frontend dan backend harus selalu memakai realm/client config yang sama (lihat `_docs/audit/audit-2026-07-31.md` sisi frontend #6 tentang duplikasi env var Keycloak) — perubahan konfigurasi Keycloak harus disinkronkan di kedua sisi.

---

*ADR tidak pernah dihapus/diedit setelah Accepted. Kalau keputusan berubah, buat ADR baru yang mem-supersede ADR ini.*
