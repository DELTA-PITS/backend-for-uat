# AI Review Checklist

Checklist yang WAJIB dijalankan Claude sendiri sebelum menyatakan pekerjaan selesai — sebelum lapor ke user.

- [ ] `ruff check` / type hints konsisten — 0 error
- [ ] Test golden path + minimal edge case sudah dijalankan (pytest untuk unit, atau diminta user jalankan kalau butuh stack Docker penuh)
- [ ] Tidak ada TODO/FIXME/dead code baru tersisa tanpa alasan jelas
- [ ] Tidak ada data sensitif ter-expose (log, response API, file `.env*`/backup yang ter-stage git)
- [ ] Konsisten dengan requirement di BRD/Design Doc — tidak ada penyimpangan tanpa konfirmasi
- [ ] Semua file yang seharusnya diubah (sesuai Design Doc) sudah diubah — tidak ada yang terlewat
- [ ] Dokumentasi terkait diupdate kalau ada perubahan signifikan
- [ ] Tidak melanggar "Prinsip Tidak Bisa Diganggu Gugat" di `CLAUDE.md` project ini
- [ ] Kalau menyentuh auth/`TEST_MODE`: sudah dicek jalur eksekusinya benar-benar aktif (bukan cuma "ada di kode tapi tidak pernah dipanggil")
