"""Database connection and session management."""

import os

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Read the repository's .env for processes started on the host - alembic, the
# MCP server, uvicorn under `langgraph dev`. Variables already in the
# environment win, so a container configured by compose is unaffected.
load_dotenv(find_dotenv(usecwd=True))

# The default is the host's view of the compose-published port. Inside the
# compose network the backend container gets its own URL (hostname
# `postgres`) from docker-compose.yml, so this default is never used there.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://axonrelay:axonrelay_dev@localhost:5432/axonrelay")

# SQLAlchemy engine
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Verify connections before using them
    pool_size=5,
    max_overflow=10,
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db():
    """Dependency for getting database sessions in FastAPI routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
