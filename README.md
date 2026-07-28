# PITS Backend

Backend service for the **Public Information Trust System (PITS)** proof of concept. It provides authenticated document registration, public integrity verification, registry storage, and local blockchain anchoring.

## Repository structure

```text
V2-Backend/
├── conf/                         # Dynaconf application settings
├── docker/
│   ├── realms/realm-export.json # Reproducible local Keycloak realm
│   ├── .env.example             # Docker environment template
│   ├── Dockerfile               # Backend image
│   └── docker-compose.yml       # PostgreSQL, Keycloak, Anvil, backend
├── src/trustmark/
│   ├── api/v1/documents.py      # Register, verify, and registry-list routes
│   ├── infra/auth/keycloak.py   # JWT validation and role enforcement
│   ├── infra/blockchain_connector.py
│   ├── infra/db.py
│   ├── infra/hash_engine.py
│   ├── registry/models.py       # Registry database model
│   └── main.py                  # FastAPI application
└── tests/                       # Unit and Locust tests
```

## Local deployment

### 1. Create the Docker environment

```bash
cp docker/.env.example docker/.env
```

The example uses the first deterministic Anvil development account. It is suitable only for local proof-of-concept testing.

### 2. Start all backend services

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build -d
```

### 3. Check service status

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml ps
curl http://localhost:41012/health
```

Expected health response:

```json
{"status":"ok"}
```

Available services:

- Backend API: `http://localhost:41012`
- Swagger: `http://localhost:41012/docs`
- Keycloak: `http://localhost:8080`
- Anvil JSON-RPC: `http://localhost:8545`
- PostgreSQL: `localhost:5432`

## Local test accounts

The included realm export creates:

- Realm: `nextjs-kc`
- Client: `nextjs-web`
- Client secret: `pits-local-client-secret`
- Publisher username: `test-publisher`
- Publisher password: `test`
- Realm role: `publisher`

The Keycloak administration account comes from `docker/.env` and defaults to `admin` / `admin`.

## API summary

| Method | Route | Authentication | Purpose |
|---|---|---|---|
| GET | `/health` | None | Backend health check |
| POST | `/api/v1/register` | Bearer token + `publisher` role | Register a document hash and blockchain transaction |
| GET | `/api/v1/records` | Bearer token + `publisher` role | List registry records for the dashboard |
| POST | `/api/v1/verify` | None | Verify an uploaded document |
| GET | `/api/v1/verify/{sha256}` | None | Verify a 64-character SHA-256 value |

Registration and verification process uploaded bytes in memory. The backend does **not** retain complete uploaded files. It stores only registry metadata and cryptographic evidence.

## Important proof-of-concept limitations

- The blockchain is a local Anvil development ledger and is reset when its container state is removed.
- Default passwords and private keys are for local testing only.
- Database schema migrations are not included. For a clean stakeholder test, start with a fresh PostgreSQL volume.
- The system proves that uploaded bytes match a registered SHA-256 value and blockchain transaction. It does not establish whether the document content is factually true.

## Resetting the environment

To remove containers and the database volume:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml down -v
```

Then start the stack again using the deployment command above.
