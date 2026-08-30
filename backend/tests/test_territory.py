"""Path-overlap rules for territory claims.

These pin the *conservative* contract: `overlaps` may report a collision that
does not exist, but must never miss one that does. The false-positive cases are
asserted explicitly so a future change that tightens them is a deliberate one.
"""

import pytest

from app.territory import any_overlap, literal_prefix, normalize_path, overlaps


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("backend/app/", "backend/app"),
        ("./backend/app", "backend/app"),
        ("/backend/app", "backend/app"),
        ("backend//app", "backend/app"),
        ("backend\\app", "backend/app"),
        ("  backend/app  ", "backend/app"),
        ("", "."),
        ("/", "."),
        (".", "."),
    ],
)
def test_normalize_path(raw, expected):
    assert normalize_path(raw) == expected


def test_literal_prefix_stops_at_the_directory_before_the_wildcard():
    assert literal_prefix("backend/app/**/*.py") == "backend/app"
    assert literal_prefix("backend/app") == "backend/app"
    assert literal_prefix("*.py") == ""


class TestPlainPaths:
    def test_identical_paths_overlap(self):
        assert overlaps("backend/app/crud.py", "backend/app/crud.py")

    def test_parent_directory_covers_its_files(self):
        assert overlaps("backend/app", "backend/app/crud.py")
        assert overlaps("backend/app/crud.py", "backend/app")

    def test_trailing_slash_is_the_same_claim(self):
        assert overlaps("backend/app/", "backend/app")

    def test_siblings_do_not_overlap(self):
        assert not overlaps("backend/app", "frontend/src")
        assert not overlaps("backend/app/crud.py", "backend/app/models.py")

    def test_prefix_must_stop_at_a_path_boundary(self):
        # "backend/app" must not be read as a prefix of "backend/application".
        assert not overlaps("backend/app", "backend/application")

    def test_repo_root_covers_everything(self):
        assert overlaps(".", "backend/app/crud.py")
        assert overlaps("backend/app/crud.py", "")


class TestGlobs:
    def test_glob_matching_a_file(self):
        assert overlaps("backend/app/*.py", "backend/app/crud.py")

    def test_glob_reaches_into_a_claimed_directory(self):
        assert overlaps("backend/**/*.py", "backend/app")

    def test_glob_rooted_elsewhere_does_not_reach(self):
        assert not overlaps("frontend/**/*.ts", "backend/app")

    def test_leading_wildcard_touches_everything(self):
        assert overlaps("*.py", "backend/app/crud.py")
        assert overlaps("**/conftest.py", "frontend/src")

    def test_two_globs_sharing_a_prefix_are_reported_conservatively(self):
        # A deliberate false positive: no single file matches both, but the
        # comparison does not reason about wildcard tails. Documented in
        # app/territory.py - flagging costs a glance, missing costs an edit.
        assert overlaps("backend/**/*.py", "backend/**/*.md")

    def test_two_globs_under_different_roots_do_not_overlap(self):
        assert not overlaps("backend/**/*.py", "frontend/**/*.py")


def test_any_overlap_reports_every_colliding_pair():
    pairs = any_overlap(
        ["backend/app", "docs"],
        ["backend/app/crud.py", "frontend/src"],
    )
    assert pairs == [("backend/app", "backend/app/crud.py")]


def test_any_overlap_is_empty_when_disjoint():
    assert any_overlap(["backend"], ["frontend"]) == []
