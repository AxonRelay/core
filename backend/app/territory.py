"""Path-overlap logic for territory claims.

A *territory claim* is an advisory lease an agent takes on part of a repository
("I am editing ``backend/app/`` for the next 45 minutes"). Two claims conflict
when their path patterns can touch the same file and at least one of them is
exclusive. This module answers only the geometric half of that question: **can
these two patterns touch the same file?**

Patterns are repo-relative POSIX paths, optionally with ``fnmatch`` wildcards
(``*``, ``?``, ``[...]``; ``**`` is accepted and behaves like ``*``). A pattern
without a wildcard is a *prefix* claim: ``backend/app`` covers
``backend/app/crud.py``. The bare repo root is spelled ``.`` and covers
everything.

**This comparison is deliberately conservative: it may report an overlap that
does not exist, but never misses one that does.** Two wildcard patterns that
share a literal directory prefix are treated as overlapping without comparing
their wildcard tails, so ``src/**/*.py`` and ``src/**/*.md`` are reported as
overlapping even though no single file matches both. Claims are advisory
signals meant to make an agent go look before it writes, so a false "go look"
costs a glance while a false "all clear" costs a lost edit.
"""

from __future__ import annotations

from fnmatch import fnmatch

WILDCARD_CHARS = ("*", "?", "[")

#: The pattern denoting "the whole repository".
REPO_ROOT = "."


def normalize_path(pattern: str) -> str:
    """Canonicalize a claim pattern so equal paths compare equal.

    Backslashes become ``/``, leading ``./`` and ``/`` are stripped, duplicate
    slashes collapse, and a trailing ``/`` is dropped (``backend/app/`` and
    ``backend/app`` are the same claim). An empty result means the repo root.
    """
    path = pattern.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    while "//" in path:
        path = path.replace("//", "/")
    path = path.rstrip("/")
    return path or REPO_ROOT


def has_wildcard(pattern: str) -> bool:
    """True when the pattern contains an fnmatch metacharacter."""
    return any(char in pattern for char in WILDCARD_CHARS)


def literal_prefix(pattern: str) -> str:
    """The wildcard-free leading *directory* of a pattern.

    ``backend/app/**/*.py`` -> ``backend/app``. A pattern that starts with a
    wildcard has no literal prefix and returns ``""`` (it can match anywhere).
    """
    positions = [pattern.find(char) for char in WILDCARD_CHARS if char in pattern]
    if not positions:
        return pattern
    head = pattern[: min(positions)]
    cut = head.rfind("/")
    return head[:cut] if cut != -1 else ""


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    """True when ``ancestor`` is ``descendant`` itself or contains it."""
    if ancestor == REPO_ROOT:
        return True
    if descendant == REPO_ROOT:
        return False
    return ancestor == descendant or descendant.startswith(ancestor + "/")


def _glob_touches(glob: str, path: str) -> bool:
    """Can wildcard pattern ``glob`` match ``path`` or anything beneath it?"""
    if fnmatch(path, glob):
        return True
    prefix = literal_prefix(glob)
    if prefix == "":
        # Leading wildcard - it can land anywhere in the tree.
        return True
    # Either the glob is rooted inside `path` (so it reaches under it), or
    # `path` lies inside the glob's literal prefix (so the glob may reach it).
    return _is_ancestor(path, prefix) or _is_ancestor(prefix, path)


def overlaps(a: str, b: str) -> bool:
    """True when two claim patterns can touch the same file.

    Conservative by design - see the module docstring.
    """
    a, b = normalize_path(a), normalize_path(b)
    if a == REPO_ROOT or b == REPO_ROOT:
        return True

    a_wild, b_wild = has_wildcard(a), has_wildcard(b)

    if not a_wild and not b_wild:
        return _is_ancestor(a, b) or _is_ancestor(b, a)

    if a_wild and b_wild:
        prefix_a, prefix_b = literal_prefix(a), literal_prefix(b)
        if prefix_a == "" or prefix_b == "":
            return True
        return _is_ancestor(prefix_a, prefix_b) or _is_ancestor(prefix_b, prefix_a)

    glob, path = (a, b) if a_wild else (b, a)
    return _glob_touches(glob, path)


def any_overlap(patterns_a: list[str], patterns_b: list[str]) -> list[tuple[str, str]]:
    """Every overlapping ``(a, b)`` pattern pair between two claim path sets."""
    return [(a, b) for a in patterns_a for b in patterns_b if overlaps(a, b)]
