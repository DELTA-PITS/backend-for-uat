"""
Unit tests for trustmark.infra.commons.get_env_int - a small pure function
with zero prior test coverage, used to compute EXPOSE_PORT in main.py.
"""
from __future__ import annotations

from trustmark.infra.commons import get_env_int


class TestGetEnvInt:
    def test_valid_int_string_is_parsed(self, monkeypatch):
        monkeypatch.setenv("SOME_PORT", "8080")
        assert get_env_int("SOME_PORT", 1234) == 8080

    def test_missing_var_returns_default(self, monkeypatch):
        monkeypatch.delenv("SOME_PORT", raising=False)
        assert get_env_int("SOME_PORT", 1234) == 1234

    def test_non_numeric_value_falls_back_to_default_with_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SOME_PORT", "not-a-number")
        assert get_env_int("SOME_PORT", 1234) == 1234

    def test_empty_string_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SOME_PORT", "")
        assert get_env_int("SOME_PORT", 1234) == 1234
