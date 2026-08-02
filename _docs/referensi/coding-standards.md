# Coding Standards — PITS Backend

## 1. Code Style

- **Bahasa/framework**: Python 3.12, FastAPI, SQLAlchemy, Pydantic.
- **Package manager**: `uv` — selalu jalankan `uv sync --frozen` untuk install, jangan pakai `pip install` langsung. Tambah dependency lewat `uv add`, bukan edit `pyproject.toml` manual tanpa lock ulang.
- **Linter**: `ruff` — jalankan `ruff check` dan `ruff format` sebelum commit. Catatan: saat ini `ruff` salah ditaruh di `[project.dependencies]` (harusnya dev-dependency) dan belum ada `[tool.ruff]` config — kalau menambah konfigurasi linter, taruh di `pyproject.toml` bagian `[tool.ruff]`.
- **Struktur folder**: `api/v1/` untuk route handler, `infra/` untuk integrasi teknis (auth, db, blockchain, hashing), `registry/` untuk model ORM domain registry, `services/` untuk integrasi eksternal (saat ini banyak file kosong/belum diimplementasikan).
- **Type hints**: wajib untuk semua fungsi publik (parameter + return type) — konsisten dengan pola yang sudah ada di `keycloak.py`, `blockchain_connector.py`. Tidak ada type checker (mypy/pyright) terpasang saat ini — pertimbangkan menambahkannya sebelum keluar dari tahap PoC.
- **Docstring**: fungsi publik yang kompleks (auth verification, blockchain transaction, hashing) wajib docstring singkat menjelaskan parameter/return/exception. Endpoint FastAPI wajib `summary`/`description`/`response_model` untuk dokumentasi Swagger yang layak (lihat gap di `api/v1/documents.py` vs `api/v1/metrics.py` yang sudah rapi).

## 2. Project Rules

- **Format response API**: endpoint sukses mengembalikan objek JSON langsung (bukan wrapper `{success, data}`) — konsisten dengan pola yang sudah ada di `documents.py`/`metrics.py`. Ikuti pola ini untuk endpoint baru, jangan campur gaya wrapper berbeda.
- **Error handling**: selalu gunakan `HTTPException` FastAPI dengan status code yang sesuai (400 input tidak valid, 401/403 auth, 500 kegagalan infra) — jangan biarkan exception mentah bocor ke response tanpa `try/except`.
- **Timestamp**: selalu UTC di database, konversi timezone dilakukan di frontend — jangan simpan/hitung timezone lokal di backend.
- **Hash**: selalu SHA-256 dari byte mentah dokumen — jangan ganti algoritma tanpa ADR baru (breaking change untuk semua record lama).
- **Auth**: setiap endpoint baru yang harus dibatasi publisher **wajib** memakai `require_roles("publisher")` dari `infra/auth/keycloak.py` — jangan implementasi cek role manual ad-hoc di route handler.
- **`TEST_MODE`**: hanya untuk lingkungan test/CI. Sebelum menambah logic yang bergantung pada `TEST_MODE`, cek dulu apakah wiring-nya benar-benar aktif di jalur eksekusi yang dipakai (lihat bug wiring di `_docs/audit/audit-2026-07-31.md` #2 — override hanya aktif di blok `if __name__ == "__main__"`).
- **Konfigurasi**: semua nilai konfigurasi lewat Dynaconf (`conf/settings.toml` + env var) — jangan hardcode port, URL, atau path di kode.
- **Logging**: gunakan `logging` module (bukan `print()`) untuk semua output operasional — lihat gap `print()` debug di `main.py:33-34` yang perlu dibersihkan.
- **File upload**: selalu proses di memory, jangan pernah menyimpan/menulis isi dokumen lengkap ke disk atau database (constitution project, lihat `CLAUDE.md`).
