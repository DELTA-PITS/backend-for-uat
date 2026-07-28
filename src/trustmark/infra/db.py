import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from trustmark.infra.commons import settings

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    db_path = settings.get("DB_PATH")
    if not db_path:
        raise RuntimeError("DATABASE_URL is not set and settings.DB_PATH is missing.")
    DATABASE_URL = f"sqlite:///{db_path}"

engine_kwargs = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    # SQLite needs this flag for FastAPI's threaded request handling.
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
