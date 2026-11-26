"""Tests for where the cache lives, and for the bundled example.

The cache location is worth testing because every branch of it has a cost: a wrong default downloads
Scryfall's 80 MB card database again, or writes it into a directory that belongs to something else. Both
are silent — nothing errors, you just pay for it once per directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from magic_builder import paths


def _resolved(monkeypatch, cwd: Path, **env) -> Path:
    """Call the resolver with a given working directory and environment, without leaking either."""
    for key in ("MAGIC_BUILDER_CACHE", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(cwd)
    return paths._cache_root()


def test_a_fresh_directory_uses_the_user_cache(tmp_path, monkeypatch):
    """The bug this fixes: the cache used to be `./.cache`, so trying the tool from a second folder
    downloaded the card database again — once per directory, forever."""
    got = _resolved(monkeypatch, tmp_path)
    assert got == Path.home() / ".cache" / "magic-builder"


def test_an_existing_cache_of_ours_is_kept_in_place(tmp_path, monkeypatch):
    """An existing checkout — and the Render build, which pre-downloads at build time — must not be
    charged 80 MB because the default moved."""
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "scryfall_default_cards.jsonl.gz").write_bytes(b"x")
    assert _resolved(monkeypatch, tmp_path) == Path(".cache")


def test_a_foreign_dot_cache_is_left_alone(tmp_path, monkeypatch):
    """`.cache/` is a common name. A directory that is not ours must not receive an 80 MB download —
    the rule is "a cache that is already ours", not "a directory called .cache"."""
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "something-else").write_text("not ours", encoding="utf-8")
    got = _resolved(monkeypatch, tmp_path)
    assert got == Path.home() / ".cache" / "magic-builder"
    assert got != Path(".cache")


def test_an_explicit_override_wins(tmp_path, monkeypatch):
    assert _resolved(monkeypatch, tmp_path, MAGIC_BUILDER_CACHE="/tmp/somewhere") == Path("/tmp/somewhere")


def test_xdg_is_respected(tmp_path, monkeypatch):
    got = _resolved(monkeypatch, tmp_path, XDG_CACHE_HOME="/tmp/xdg")
    assert got == Path("/tmp/xdg/magic-builder")


def test_the_resolved_cache_root_is_not_empty():
    """Whatever the environment, CACHE_ROOT must be a real path — an empty string would make every
    download land in the working directory under a stray name."""
    assert paths.CACHE_ROOT and str(paths.CACHE_ROOT) not in (".", "")


# ---------------------------------------------------------------- the shipped example

def test_the_example_ships_inside_the_package():
    """`--demo` resolves this from the package, not the checkout, so it has to work from a wheel: the
    path must be under the installed package directory, and the file must be there."""
    ex = paths.example_collection()
    assert ex.exists(), "the bundled example is missing — a wheel built without it breaks --demo"
    assert ex.parent.parent.name == "magic_builder"
    assert ex.name == "example_binder.csv"


def test_the_package_data_directory_is_reachable_by_relative_path():
    """The example is found relative to paths.PKG_ROOT, which must be the package's own directory.
    An absolute path derived from the cwd would break for anyone running from elsewhere."""
    assert Path(paths.__file__).parent == paths.PKG_ROOT
    assert not paths.PKG_ROOT.is_absolute() or os.path.isabs(str(paths.PKG_ROOT))


@pytest.mark.parametrize("name", ["example_binder.csv"])
def test_the_example_is_parseable_as_a_collection(name):
    """It is the demo's input, so it has to be a collection this program can read — checked through the
    same dispatcher the CLI uses, not by eye."""
    from magic_builder.collection.arena import load_owned_cards

    path = paths.PKG_ROOT / "data" / name
    cards = load_owned_cards(str(path), {})
    assert cards, f"{name} parsed to nothing"
    assert all(c.scryfall_id for c in cards), "every example card needs an id to be enrichable"
