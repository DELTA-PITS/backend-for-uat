# ADR-001 — Anchoring Hash ke Blockchain Lokal Anvil (bukan Testnet/Mainnet Publik)

**Status:** Accepted
**Tanggal:** 2026-07-31 (didokumentasikan retroaktif — keputusan sudah diimplementasikan sejak awal project)

## Konteks

PITS butuh cara untuk membuktikan bahwa hash dokumen terdaftar tidak bisa diubah setelah didaftarkan (immutability). Opsi paling umum adalah anchoring ke blockchain publik (testnet seperti Sepolia, atau mainnet). Karena project ini masih tahap proof-of-concept untuk UAT stakeholder, dibutuhkan lingkungan yang cepat di-setup, gratis, dan reproducible tanpa dependency ke jaringan eksternal.

## Keputusan

Gunakan **Anvil** (local EVM development chain, bagian dari Foundry) yang dijalankan sebagai container lokal (`docker-compose.yml`), dengan akun deterministik bawaan Anvil sebagai signer transaksi.

## Alternatif yang Dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Keputusan |
|------|-----------|-----------|-----------|
| Testnet publik (Sepolia dsb) | Bukti lebih meyakinkan ke stakeholder eksternal, tidak reset saat container dihapus | Butuh faucet/gas, koneksi internet, latency, ketergantungan pihak ketiga | ❌ Tidak dipilih untuk tahap PoC |
| Mainnet publik | Bukti permanen dan paling kuat secara bisnis | Biaya gas nyata, tidak cocok untuk iterasi cepat PoC | ❌ Tidak dipilih |
| Anvil lokal — dipilih | Gratis, deterministik, cepat untuk demo/UAT, tidak butuh koneksi eksternal | Bukan ledger permanen — reset saat volume container dihapus, tidak membuktikan apa pun ke pihak eksternal yang tidak mengakses instance yang sama | ✅ Dipilih |

## Konsekuensi

**Positif:** Setup lokal cepat, tidak ada biaya, cocok untuk UAT/demo internal, deterministik untuk testing.

**Negatif/Risiko:** Anchoring ini **tidak** membuktikan apa pun ke pihak eksternal yang tidak punya akses ke instance Anvil yang sama — ini murni untuk membuktikan konsep (arsitektur), bukan untuk klaim "immutable" yang bisa diverifikasi independen oleh publik luas. Sebelum keluar dari tahap PoC ke produksi, keputusan ini **harus ditinjau ulang** — kemungkinan besar perlu pindah ke testnet/mainnet publik atau layanad anchoring pihak ketiga.

**Aturan turunan:** Jangan mengklaim ke stakeholder bahwa bukti anchoring saat ini "immutable secara publik" — selalu jelaskan ini masih PoC dengan chain lokal (lihat README § limitasi PoC).

---

*ADR tidak pernah dihapus/diedit setelah Accepted. Kalau keputusan berubah (misal pindah ke testnet), buat ADR baru yang mem-supersede ADR ini.*
