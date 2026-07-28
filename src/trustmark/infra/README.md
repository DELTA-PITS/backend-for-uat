# Blockchain Connector Service

This module provides a small service wrapper around Web3 for writing a value to the chain as transaction input data and reading it back.

## Configuration

- `rpc_url`: HTTP RPC endpoint for the EVM node (for example, `http://127.0.0.1:8545`).
- `private_key`: Hex-encoded private key with a `0x` prefix.

## Usage

```python
from trustmark.infra.blockchain_connector import Blockchain_connector

service = Blockchain_connector(rpc_url="http://127.0.0.1:8545", private_key="0x...")

tx_hash = service.create_transaction("Blockchain demo from PITS")
block_number = service.wait_for_receipt(tx_hash)
value = service.read_transaction_value(tx_hash)

print(tx_hash, block_number, value)
```

## API

- `create_transaction(value: str) -> str`
  - Signs and sends a transaction with the payload prefix `VALUE:` and returns the transaction hash as a hex string.
- `wait_for_receipt(tx_hash: str) -> int`
  - Waits until the transaction is mined and returns the block number.
- `read_transaction_value(tx_hash: str) -> str`
  - Reads the transaction input data and returns the decoded value string.

## Errors

All connection and decoding problems raise a `fastapi.HTTPException` with status code `500`.
