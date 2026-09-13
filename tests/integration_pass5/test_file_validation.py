"""
PASS 5 — Section 7: File validation integration (FILE-01..12).

SCOPE: this local stack runs the FastAPI backend directly on :41012 with no
frontend dev server and no nginx in front of it (docker-compose.yml in this
repo only defines postgres/keycloak/anvil/trustmark-app). So these tests
exercise the BACKEND HTTP upload path only. Frontend (Next.js Dropzone
client-side checks) and nginx (client_max_body_size) validation layers are
documented from source/prior QA passes, not re-verified here - see
Findings section of the pass-5 report for what is/isn't covered.

Do not call extension/MIME acceptance "PDF content validation" unless actual
magic-byte/content parsing occurs - documents.py::_read_upload only checks
byte length, nothing else. This file's assertions are written to describe
CURRENT behaviour, and are explicit about which layer (if any) is doing the
rejecting.
"""
from __future__ import annotations

from conftest import register, unique_pdf_bytes


class TestFileValidation:
    def test_file_01_valid_pdf(self, token_a):
        content = unique_pdf_bytes("FILE-01")
        resp = register(token_a, content, filename="valid.pdf", content_type="application/pdf")
        assert resp.status_code == 200, resp.text

    def test_file_02_txt_renamed_pdf(self, token_a):
        """A plain text file, given a .pdf filename and application/pdf
        content-type by the CLIENT (as any HTTP client can freely set) -
        documents.py does not inspect actual bytes for a %PDF magic number."""
        content = b"this is just plain text, not a pdf, marker=" + unique_pdf_bytes("FILE-02")
        resp = register(token_a, content, filename="fake.pdf", content_type="application/pdf")
        print(f"FILE-02: status={resp.status_code}")
        assert resp.status_code == 200, (
            "known gap: backend accepts non-PDF byte content as long as the "
            f"client claims filename/content-type; got {resp.status_code}: {resp.text}"
        )

    def test_file_03_fake_mime_type(self, token_a):
        content = unique_pdf_bytes("FILE-03")  # real %PDF-1.4 bytes
        resp = register(token_a, content, filename="doc.pdf", content_type="image/png")
        print(f"FILE-03: status={resp.status_code} (real PDF bytes, spoofed MIME)")
        assert resp.status_code == 200, resp.text

    def test_file_04_corrupt_pdf_header(self, token_a):
        content = b"%PDF-CORRUPT-NOT-REAL\x00\x01\x02" + b"garbage" * 20
        resp = register(token_a, content, filename="corrupt.pdf", content_type="application/pdf")
        print(f"FILE-04: status={resp.status_code} - no magic-byte/structure validation exists, "
              f"so a corrupt PDF is accepted exactly like a valid one")
        assert resp.status_code == 200, resp.text

    def test_file_05_empty_pdf(self, token_a):
        resp = register(token_a, b"", filename="empty.pdf", content_type="application/pdf")
        assert resp.status_code == 400, resp.text
        assert "empty" in resp.json()["detail"].lower()

    def test_file_06_exactly_at_size_limit(self, token_a):
        import requests

        limit_resp = requests.get("http://127.0.0.1:41012/health", timeout=5)
        assert limit_resp.status_code == 200
        # MAX_UPLOAD_BYTES from docker/.env for this stack
        max_bytes = 20 * 1024 * 1024
        marker = unique_pdf_bytes("FILE-06")
        content = marker + b"0" * (max_bytes - len(marker))
        assert len(content) == max_bytes
        resp = register(token_a, content, filename="at-limit.pdf")
        print(f"FILE-06: status={resp.status_code}, size={len(content)}")
        assert resp.status_code == 200, resp.text

    def test_file_07_one_byte_over_limit(self, token_a):
        max_bytes = 20 * 1024 * 1024
        marker = unique_pdf_bytes("FILE-07")
        content = marker + b"0" * (max_bytes - len(marker) + 1)
        assert len(content) == max_bytes + 1
        resp = register(token_a, content, filename="over-limit.pdf")
        print(f"FILE-07: status={resp.status_code}, size={len(content)}")
        assert resp.status_code == 413, resp.text

    def test_file_08_unicode_filename(self, token_a):
        content = unique_pdf_bytes("FILE-08")
        resp = register(token_a, content, filename="文件-döcümeñt-📄.pdf")
        print(f"FILE-08: status={resp.status_code}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["filename"]

    def test_file_09_very_long_filename(self, token_a):
        long_name = "a" * 500 + ".pdf"
        content = unique_pdf_bytes("FILE-09")
        resp = register(token_a, content, filename=long_name)
        print(f"FILE-09: status={resp.status_code}, filename_len={len(long_name)}")
        # original_filename column is String(255) - a filename over 255 chars
        # exercises the DB column boundary.
        if resp.status_code == 200:
            print(f"FILE-09: filename stored/returned as len={len(resp.json().get('filename') or '')}")
        else:
            print(f"FILE-09: rejected: {resp.text[:300]}")

    def test_file_10_path_like_filename(self, token_a):
        content = unique_pdf_bytes("FILE-10")
        resp = register(token_a, content, filename="../../etc/passwd.pdf")
        print(f"FILE-10: status={resp.status_code}, stored filename={resp.json().get('filename') if resp.status_code == 200 else resp.text[:200]}")
        # documents.py stores file.filename as-is (original_filename column) and
        # never uses it as a filesystem path (files are never written to disk -
        # see finding on hashing-only, no disk storage) - so path traversal via
        # filename has no filesystem effect here, only a DB/display concern.
        assert resp.status_code == 200, resp.text

    def test_file_11_same_bytes_different_filename(self, token_a):
        content = unique_pdf_bytes("FILE-11")
        r1 = register(token_a, content, filename="name-one.pdf")
        r2 = register(token_a, content, filename="name-two.pdf")
        assert r1.status_code == 200 and r2.status_code == 200
        b1, b2 = r1.json(), r2.json()
        print(f"FILE-11: first stored filename={b1['filename']}, second already_existed={b2['already_existed']}")
        assert b2["already_existed"] is True
        assert b2["record_id"] == b1["record_id"]
        # content_hash is filename-independent by design (hash of bytes only)
        assert b2["filename"] == b1["filename"], (
            "duplicate detection returns the ORIGINALLY stored filename, not "
            "the second request's filename - confirms filename is not part of "
            "the identity/hash, only content bytes are"
        )

    def test_file_12_one_byte_different_pdf(self, token_a):
        base = unique_pdf_bytes("FILE-12")
        modified = bytearray(base)
        modified[-1] ^= 0x01
        r1 = register(token_a, bytes(base), filename="base.pdf")
        r2 = register(token_a, bytes(modified), filename="modified.pdf")
        assert r1.status_code == 200 and r2.status_code == 200
        b1, b2 = r1.json(), r2.json()
        print(f"FILE-12: hash1={b1['content_hash']}, hash2={b2['content_hash']}")
        assert b1["content_hash"] != b2["content_hash"]
        assert b2["already_existed"] is False
        assert b1["record_id"] != b2["record_id"]
