import logging
import os
from contextlib import asynccontextmanager

import emoji
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from trustmark.infra.auth.keycloak import Principal, get_current_principal
import uvicorn

from trustmark.api.v1 import metrics, documents
from trustmark.infra.commons import get_env_int, settings, project_details
from trustmark.infra.db import engine, Base
from trustmark.infra.auth.keycloak import require_roles


@asynccontextmanager
async def lifespan(application: FastAPI):
    """
    Lifespan event handler for the FastAPI application.

    This function is executed during the startup of the FastAPI application.
    It initializes the database, iterates through saved bridge plugin directories,
    and prints available bridge classes.

    Args:
        application (FastAPI): The FastAPI application.

    Yields:
        None: The context manager does not yield any value.

    """
    print("====== start up==========")
    print(emoji.emojize(":thumbs_up:"))
    logging.info("Starting up")
    Base.metadata.create_all(bind=engine)
    yield


_project = project_details()

app = FastAPI(
    title=_project["title"],
    description=_project["description"],
    version=_project.get("version", os.environ.get("acp_version", "unknown")),
    lifespan=lifespan,
)


def _get_cors_origins() -> list[str]:
    configured = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


EXPOSE_PORT = get_env_int("EXPOSE_PORT", 41012)
log_config = uvicorn.config.LOGGING_CONFIG
logging.basicConfig(
    filename=settings.LOG_FILE, level=settings.LOG_LEVEL, format=settings.LOG_FORMAT
)

@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(
    documents.router_upload,
    prefix=f"{settings.API_PREFIX}",
    tags=["documents"],
    dependencies=[Depends(get_current_principal)],
)

app.include_router(
    documents.router_verify,
    prefix=f"{settings.API_PREFIX}",
    tags=["documents"],
)
app.include_router(
    metrics.router,
    prefix=f"{settings.API_PREFIX}",
    tags=["metrics"],
)


def main():
    logging.info("Starting up")
    uvicorn.run(app, host="0.0.0.0", port=EXPOSE_PORT, log_config=log_config)


async def override_get_current_principal():
    return Principal(
        sub="test-user",
        username="testuser",
        email="testuser@example.com",
        roles={"publisher"},
        claims={},
    )


if __name__ == "__main__":
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        app.dependency_overrides[get_current_principal] = override_get_current_principal

    main()
