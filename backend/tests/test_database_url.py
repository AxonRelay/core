"""The engine URL must name the driver we actually install (psycopg2)."""

import os
import pathlib
import subprocess
import sys

from app.database import with_explicit_driver


def test_bare_postgresql_url_is_pinned_to_psycopg2():
    assert with_explicit_driver("postgresql://u:p@h:5432/db") == "postgresql+psycopg2://u:p@h:5432/db"


def test_postgres_alias_is_pinned_to_psycopg2():
    assert with_explicit_driver("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"


def test_alembic_accepts_a_percent_encoded_password():
    """env.py must escape ``%`` for Alembic's interpolating ConfigParser.

    Offline mode (``--sql``) reads ``sqlalchemy.url`` back from the config and
    needs no server, so it exercises env.py end to end. Only the first
    revision is rendered: later ones read data and cannot run offline.
    """
    backend_dir = pathlib.Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "001", "--sql"],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": "postgresql://u:p%40ss@localhost:5432/db"},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_url_that_names_a_driver_is_left_alone():
    for url in ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg2://u:p@h/db", "sqlite:///:memory:"):
        assert with_explicit_driver(url) == url
