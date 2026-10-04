"""
Startup guard in trustmark.main: refuse to boot on a wrong/invalid chain configuration, but do not
crash because the RPC provider is briefly unreachable (the chain ID is re-checked on first use).

Run with:
    python -m pytest tests/trustmark/test_startup_chain_check.py
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from trustmark import main
from trustmark.infra.blockchain_connector import ChainConfigError, ChainMismatch


@pytest.fixture(autouse=True)
def chain_env(monkeypatch):
    monkeypatch.setenv("BLOCKCHAIN_RPC_URL", "http://rpc.invalid")
    monkeypatch.setenv("BLOCKCHAIN_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("BLOCKCHAIN_CHAIN_ID", "11155111")


def test_matching_chain_boots():
    with patch.object(main.BlockchainConnector, "verify_chain", return_value=None):
        main._check_blockchain_chain()


def test_chain_mismatch_stops_startup_with_clear_message():
    with patch.object(main.BlockchainConnector, "verify_chain", side_effect=ChainMismatch(11155111, 31337)):
        with pytest.raises(RuntimeError, match="configured 11155111, RPC reports 31337"):
            main._check_blockchain_chain()


def test_unreachable_rpc_does_not_stop_startup():
    unreachable = HTTPException(500, detail="Blockchain connection failed")
    with patch.object(main.BlockchainConnector, "verify_chain", side_effect=unreachable):
        main._check_blockchain_chain()


def test_missing_chain_id_stops_startup(monkeypatch):
    monkeypatch.delenv("BLOCKCHAIN_CHAIN_ID")
    with pytest.raises(ChainConfigError):
        main._check_blockchain_chain()


def test_rpc_url_bearing_loggers_are_not_at_debug():
    """DEBUG records from web3/urllib3 include the full RPC URL (and so any provider API key) in the log file."""
    import logging

    assert logging.getLogger("web3").level >= logging.WARNING
    assert logging.getLogger("urllib3").level >= logging.WARNING
