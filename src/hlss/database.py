"""
Database configuration and session management.
"""

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateSchema

from hlss.config import get_settings


settings = get_settings()


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    # Set the schema for all models
    __table_args__ = {"schema": settings.database_schema}


engine = create_engine(
    str(settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


@event.listens_for(engine, "connect")
def set_search_path(dbapi_connection, connection_record):
    """Set the search path to use the configured schema."""
    cursor = dbapi_connection.cursor()
    cursor.execute(f"SET search_path TO {settings.database_schema}, public")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_exists() -> None:
    """Create the schema if it doesn't exist."""
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {settings.database_schema}"))
        conn.commit()


def init_db() -> None:
    """Initialize database schema and tables."""
    # First ensure the schema exists
    ensure_schema_exists()
    # Then create all tables
    Base.metadata.create_all(bind=engine)
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
