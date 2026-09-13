"""
PASS 5 — Section 4: Multi-publisher data isolation (ISO-01..05).

Directly tests the known IDOR finding (documents.py::list_records has no
issuer_id filter) using two REAL Keycloak-authenticated publishers against
the live local stack - not source inspection, not mocks. Runtime evidence
only.
"""
from __future__ import annotations

from conftest import register, list_records, unique_pdf_bytes


class TestIsolation:
    def test_iso_01_and_02_cross_publisher_visibility(self, token_a, token_b):
        # A registers a document
        reg_a = register(token_a, unique_pdf_bytes("ISO-A"), filename="publisher-a-secret.pdf")
        assert reg_a.status_code == 200, reg_a.text
        body_a = reg_a.json()

        # B registers a document
        reg_b = register(token_b, unique_pdf_bytes("ISO-B"), filename="publisher-b-secret.pdf")
        assert reg_b.status_code == 200, reg_b.text
        body_b = reg_b.json()

        # ISO-01: A lists records
        resp_a = list_records(token_a)
        assert resp_a.status_code == 200, resp_a.text
        records_seen_by_a = resp_a.json()["records"]
        ids_seen_by_a = {r["record_id"] for r in records_seen_by_a}

        # ISO-02: B lists records
        resp_b = list_records(token_b)
        assert resp_b.status_code == 200, resp_b.text
        records_seen_by_b = resp_b.json()["records"]
        ids_seen_by_b = {r["record_id"] for r in records_seen_by_b}

        print(f"ISO-01: publisher-a sees {len(records_seen_by_a)} records total, "
              f"includes own record_id={body_a['record_id'] in ids_seen_by_a}, "
              f"includes B's record_id={body_b['record_id'] in ids_seen_by_a}")
        print(f"ISO-02: publisher-b sees {len(records_seen_by_b)} records total, "
              f"includes own record_id={body_b['record_id'] in ids_seen_by_b}, "
              f"includes A's record_id={body_a['record_id'] in ids_seen_by_b}")

        a_leaks_b = body_b["record_id"] in ids_seen_by_a
        b_leaks_a = body_a["record_id"] in ids_seen_by_b

        assert not a_leaks_b, (
            "ISO-01 FAILED (IDOR CONFIRMED, live runtime evidence): "
            f"publisher-a's GET /records response included publisher-b's "
            f"record ({body_b['record_id']}, filename={body_b['filename']!r}). "
            "list_records() has no issuer_id filter."
        )
        assert not b_leaks_a, (
            "ISO-02 FAILED (IDOR CONFIRMED, live runtime evidence): "
            f"publisher-b's GET /records response included publisher-a's "
            f"record ({body_a['record_id']}, filename={body_a['filename']!r})."
        )

    def test_iso_03_cross_publisher_field_exposure(self, token_a, token_b):
        """If ISO-01/02 shows leakage, this confirms exactly which fields of
        the other publisher's record are exposed (all of them, per
        _record_payload's shape)."""
        reg_b = register(token_b, unique_pdf_bytes("ISO-03-B"), filename="iso-03-b-private.pdf")
        assert reg_b.status_code == 200
        body_b = reg_b.json()

        resp_a = list_records(token_a)
        assert resp_a.status_code == 200
        records = resp_a.json()["records"]
        match = next((r for r in records if r["record_id"] == body_b["record_id"]), None)

        if match is None:
            print("ISO-03: B's record not visible to A in this run (see ISO-01/02 for the "
                  "primary IDOR evidence) - cannot enumerate exposed fields from an absent record.")
            return

        exposed_fields = [k for k in ("record_id", "content_hash", "filename", "transaction_hash", "created_at", "issuer_id") if k in match]
        print(f"ISO-03: fields of publisher-b's record exposed to publisher-a: {exposed_fields}")
        print(f"ISO-03: full leaked record as seen by publisher-a: {match}")
        assert set(exposed_fields) == {"record_id", "content_hash", "filename", "transaction_hash", "created_at", "issuer_id"}

    def test_iso_04_multiple_records_per_publisher(self, token_a, token_b):
        for i in range(3):
            r = register(token_a, unique_pdf_bytes(f"ISO-04-A-{i}"))
            assert r.status_code == 200
        for i in range(3):
            r = register(token_b, unique_pdf_bytes(f"ISO-04-B-{i}"))
            assert r.status_code == 200

        resp_a = list_records(token_a)
        records = resp_a.json()["records"]
        distinct_issuers = {r["issuer_id"] for r in records}
        print(f"ISO-04: after multiple records per publisher, GET /records for A returns "
              f"{len(records)} total rows spanning {len(distinct_issuers)} distinct issuer_id "
              f"value(s): {distinct_issuers}")
        # Multiple distinct issuer_id values visible to a single principal is the
        # isolation failure repeated at scale.
        assert len(distinct_issuers) <= 1, (
            f"ISO-04: publisher-a's records listing spans {len(distinct_issuers)} distinct "
            f"issuer_id values after both publishers registered multiple documents each - "
            "confirms the isolation failure is not a one-off, it applies to the whole table."
        )

    def test_iso_05_no_direct_single_record_endpoint(self, token_a, token_b):
        """The API surface (documents.py) exposes /register, /records (list,
        collection-level), /verify, /verify/{file_hash} - there is no
        GET /records/{id} single-record endpoint to probe for a second IDOR
        vector. Documented as N/A rather than skipped silently."""
        import requests
        from conftest import BACKEND_URL

        reg = register(token_b, unique_pdf_bytes("ISO-05"))
        record_id = reg.json()["record_id"]

        resp = requests.get(f"{BACKEND_URL}/records/{record_id}", headers={"Authorization": f"Bearer {token_a}"}, timeout=10)
        print(f"ISO-05: GET /records/{{id}} (not a documented route) -> {resp.status_code}")
        assert resp.status_code == 404, "unexpected route exists at /records/{id}"
