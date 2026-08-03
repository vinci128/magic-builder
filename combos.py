"""Two-card infinite combo lookup, via Commander Spellbook.

The other four bracket criteria are properties of single cards, so `brackets.py`
reads them straight off the card database. Combos are not: whether a deck can
win on the spot depends on which *pairs* of cards happen to be in it, and no
card-level field encodes that. Commander Spellbook maintains the combo database
that answers it, and its `/estimate-bracket` endpoint already classifies each
combo the way the bracket rules need — two-card or not, and how fast it wins.

Only the combo section of that response is used. The bracket itself is computed
locally so that it still works, and still explains itself, with no network.

Responses are cached under `.cache/combos/` keyed by deck contents, so
rebuilding the same deck (the common case while tuning) costs nothing.
"""

import hashlib
import json
import time
from pathlib import Path

import requests

API_URL = "https://backend.commanderspellbook.com/estimate-bracket"
CACHE_DIR = Path(".cache/combos")
CACHE_TTL = 7 * 86400  # the combo database grows by set release, not by day
TIMEOUT = 20

# A combo that needs this much mana or more is not a turn-3 kill in a deck that
# was not built to ramp into it. Spellbook reports `speed` as the earliest turn
# the line can realistically assemble; bracket 3 tolerates late combos only.
EARLY_TURN = 6


class ComboResult:
    """What the combo lookup found, or why it found nothing."""

    __slots__ = ("checked", "error", "two_card", "other")

    def __init__(self, checked: bool, error: str = "",
                 two_card: list | None = None, other: list | None = None):
        self.checked = checked      # False when the lookup could not run
        self.error = error
        self.two_card = two_card or []   # the ones the bracket rules care about
        self.other = other or []         # 3+ card lines, reported but not gating

    @property
    def early(self) -> list:
        return [c for c in self.two_card if c["early"]]


def _cache_path(commander: str, names: list) -> Path:
    # Keyed by contents, not commander: two decks for one commander differ, and
    # the same 100 cards always classify the same way.
    digest = hashlib.sha256("\n".join([commander] + sorted(names)).encode()).hexdigest()
    return CACHE_DIR / f"{digest[:32]}.json"


def _fetch(commander: str, names: list, refresh: bool) -> dict:
    path = _cache_path(commander, names)
    if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # corrupt cache entry — refetch rather than fail the build

    body = {
        "commanders": [{"card": commander, "quantity": 1}] if commander else [],
        "main": [{"card": name, "quantity": 1} for name in names],
    }
    resp = requests.post(
        API_URL, json=body,
        headers={"User-Agent": "magic-builder/1.0"}, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _combo_json(entry: dict) -> dict:
    combo = entry.get("combo") or {}
    speed = entry.get("speed")
    return {
        "cards": [use.get("card", {}).get("name", "") for use in combo.get("uses", [])],
        "produces": [p.get("feature", {}).get("name", "")
                     for p in combo.get("produces", [])][:2],
        # Spellbook's own tag for the combo's power level (Ruthless, Spicy,
        # Core...), shown as-is rather than remapped onto the deck brackets.
        "tag": combo.get("bracketTag") or "",
        "speed": speed,
        "early": speed is not None and speed < EARLY_TURN,
    }


def find_combos(commander_name: str, deck: list, *, refresh: bool = False) -> ComboResult:
    """Classify the infinite combos present in `deck`.

    Never raises: an unreachable Spellbook yields `checked=False`, which the
    bracket reports as an unchecked criterion rather than as a clean result.
    """
    names = sorted({c.name for c in deck if not c.is_basic_filler})
    if not names:
        return ComboResult(checked=True)

    try:
        data = _fetch(commander_name, names, refresh)
    except (requests.RequestException, ValueError, OSError) as exc:
        return ComboResult(checked=False, error=str(exc))

    two_card, other = [], []
    for entry in data.get("combos") or []:
        # `relevant` drops combos that need a card the deck does not run; the
        # deck could otherwise be blamed for a line it cannot assemble.
        if not entry.get("relevant", True):
            continue
        payload = _combo_json(entry)
        if entry.get("definitelyTwoCard") or entry.get("arguablyTwoCard"):
            payload["arguable"] = not entry.get("definitelyTwoCard")
            two_card.append(payload)
        else:
            other.append(payload)

    two_card.sort(key=lambda c: (c["speed"] is None, c["speed"]))
    return ComboResult(checked=True, two_card=two_card, other=other)
