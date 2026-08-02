# Definition of Done

Kriteria eksplisit kapan sebuah task/fitur dianggap selesai. File tetap — bukan per-fitur.

- [ ] Kode berjalan sesuai acceptance criteria di BRD/Design Doc
- [ ] Golden path + edge case sudah ditest manual (atau otomatis via pytest)
- [ ] `ruff check` bersih (0 error)
- [ ] Tidak ada `print()`/debug output tertinggal (pakai `logging`)
- [ ] Tidak ada secret/credential hardcoded, dan tidak ada file `.env*`/backup baru ter-stage ke git
- [ ] Dokumentasi terkait sudah diupdate (BRD/Design Doc status → Implemented)
- [ ] Sudah di-review (self-review atau reviewer kedua)
- [ ] `tasks.md` sudah dipindahkan ke status selesai
