"""Filesystem locations shared across the package.

The cache is **per user**, not per directory. It used to default to `.cache/` under the current working
directory, which meant running the tool from a second folder downloaded Scryfall's 80 MB card database
again — the help text claimed "that is the only time it does" while each new directory paid for it
again. A CLI tool that litters the directory you happen to be in, and charges bandwidth for the
privilege, is two kinds of wrong.

Resolution order, most specific first:

1. `MAGIC_BUILDER_CACHE` — an explicit override, used as given.
2. A `.cache/` in the current directory, **if one already exists**. Kept so an existing checkout (and
   the Render build, which pre-downloads into `.cache/` at build time) keeps working unchanged rather
   than re-fetching 80 MB on deploy.
3. `$XDG_CACHE_HOME/magic-builder`, or `~/.cache/magic-builder` — the default for a new user.
"""

import os
from pathlib import Path


def _is_own_cache(path: Path) -> bool:
    """Whether `path` holds magic-builder's own cache — not just any directory called `.cache`.

    The distinction matters: `.cache/` is a common name, and a rule of "use a local `.cache` if one is
    there" would drop 80 MB into an unrelated project the user happened to run the command in. So the
    directory has to prove it is ours, by containing one of our artefacts.
    """
    if not path.is_dir():
        return False
    return ((path / "scryfall_default_cards.jsonl.gz").exists()
            or (path / "scryfall_default_cards.json").exists()
            or (path / "mtggoldfish").is_dir()
            or (path / "edhrec").is_dir())


def _cache_root() -> Path:
    if explicit := os.environ.get("MAGIC_BUILDER_CACHE"):
        return Path(explicit)
    # A cache that is already ours, in the working directory, is kept in place: an existing checkout
    # (and the Render build, which pre-downloads into `.cache/` at build time) should not re-fetch 80 MB
    # because the default moved.
    local = Path(".cache")
    if _is_own_cache(local):
        return local
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "magic-builder"


CACHE_ROOT = _cache_root()

# The example collection ships inside the package, not beside it, because `--demo` has to work for
# someone who installed from a wheel and has no checkout. Resolved from __file__ rather than the cwd
# for the same reason: a user's working directory is not the package's.
PKG_ROOT = Path(__file__).resolve().parent


def example_collection() -> Path:
    """The bundled example collection — a small synthetic binder, no personal data.

    It is real cards with real Scryfall ids, so `--demo` produces an actual deck rather than a
    placeholder; what it is not is anybody's collection.
    """
    return PKG_ROOT / "data" / "example_binder.csv"
