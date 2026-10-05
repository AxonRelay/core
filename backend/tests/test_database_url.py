"""The engine URL must name the driver we actually install (psycopg2)."""

from app.database import with_explicit_driver


def test_bare_postgresql_url_is_pinned_to_psycopg2():
    assert with_explicit_driver("postgresql://u:p@h:5432/db") == "postgresql+psycopg2://u:p@h:5432/db"


def test_url_that_names_a_driver_is_left_alone():
    for url in ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg2://u:p@h/db", "sqlite:///:memory:"):
        assert with_explicit_driver(url) == url
