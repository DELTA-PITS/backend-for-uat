"""
Unit test for a latent risk found during the 2026-09-09 source audit, in
trustmark/main.py:

    async def override_get_current_principal():
        return Principal(sub="test-user", ..., roles={"publisher"}, ...)

    if __name__ == "__main__":
        if os.environ.get("TEST_MODE", "false").lower() == "true":
            app.dependency_overrides[get_current_principal] = override_get_current_principal
        main()

This is NOT dead code protected by the `__main__` guard the way it might
look at a glance: `docker/Dockerfile` starts the container with
`CMD ["python", "-m", "trustmark.main"]`, and `python -m <module>` executes
that module with `__name__ == "__main__"` - the exact same mechanism as
running the file directly. So in the ACTUAL production container, this
guard is live, not inert.

Today `docker/.env` (and `.env.example`/`.env.save`) all pin
`TEST_MODE=False`, so the bypass is not currently active - but the
mechanism to fully disable authentication in what is otherwise a
production container is one environment-variable flip away, with no other
gate (no separate build target, no code path removed for prod images). This
test exists to make that fact explicit and to catch a future accidental
`TEST_MODE=True` in a deployed environment's `.env` as a loud, specific
failure rather than a silent security regression discovered in production.
"""
from __future__ import annotations

import runpy
from unittest.mock import patch


def _run_main_as_dunder_main():
    """Executes trustmark/main.py exactly the way `python -m trustmark.main`
    does (same __name__ == "__main__" mechanism), without actually starting
    a live uvicorn server."""
    with patch("uvicorn.run"):
        return runpy.run_module("trustmark.main", run_name="__main__")


class TestTestModeAuthBypass:
    def test_test_mode_true_installs_a_hardcoded_auth_bypass(self, monkeypatch):
        monkeypatch.setenv("TEST_MODE", "true")
        module_globals = _run_main_as_dunder_main()

        app = module_globals["app"]
        get_current_principal = module_globals["get_current_principal"]
        override_get_current_principal = module_globals["override_get_current_principal"]

        assert get_current_principal in app.dependency_overrides
        assert app.dependency_overrides[get_current_principal] is override_get_current_principal

    def test_override_principal_is_hardcoded_publisher_with_no_real_identity(self, monkeypatch):
        """Confirms exactly what an attacker (or a mistaken deploy) gets for
        free if TEST_MODE is ever true in a reachable environment: a
        `publisher`-roled principal, unconditionally, for every request,
        with no token required at all."""
        monkeypatch.setenv("TEST_MODE", "true")
        module_globals = _run_main_as_dunder_main()
        override_get_current_principal = module_globals["override_get_current_principal"]

        import asyncio

        principal = asyncio.run(override_get_current_principal())

        assert principal.sub == "test-user"
        assert "publisher" in principal.roles

    def test_test_mode_false_leaves_real_auth_in_place(self, monkeypatch):
        monkeypatch.setenv("TEST_MODE", "false")
        module_globals = _run_main_as_dunder_main()

        app = module_globals["app"]
        get_current_principal = module_globals["get_current_principal"]

        assert get_current_principal not in app.dependency_overrides

    def test_test_mode_unset_defaults_to_false_leaving_real_auth_in_place(self, monkeypatch):
        monkeypatch.delenv("TEST_MODE", raising=False)
        module_globals = _run_main_as_dunder_main()

        app = module_globals["app"]
        get_current_principal = module_globals["get_current_principal"]

        assert get_current_principal not in app.dependency_overrides
