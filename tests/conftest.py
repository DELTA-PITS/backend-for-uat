import os
import tempfile
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault("BASE_DIR", str(_BASE_DIR))
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("TEST_MODE", "true")

_db_fd, _db_path = tempfile.mkstemp(suffix=".db", prefix="trustmark-test-")
os.close(_db_fd)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path}")

os.environ.setdefault("KEYCLOAK_ISSUER", "http://keycloak.test/realms/test")
os.environ.setdefault("KEYCLOAK_ISSUER_URL", "http://keycloak.test/realms/test")
os.environ.setdefault("KEYCLOAK_AUDIENCE", "account")
os.environ.setdefault("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
os.environ.setdefault("BLOCKCHAIN_RPC_URL", "http://anvil.test:8545")
os.environ.setdefault("BLOCKCHAIN_PRIVATE_KEY", "0x" + "1" * 64)
os.environ.setdefault("CORS_ALLOW_ORIGINS", "http://localhost:3000")
