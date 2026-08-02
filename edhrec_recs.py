"""Card recommendations sourced from EDHREC, no LLM involved.

EDHREC publishes, per commander, which cards other players actually run: an
inclusion rate (how many of that commander's decks play the card) and a synergy
score (how much more often it shows up here than in decks generally). Split that
list against a collection and it answers the two questions a builder cares about:

  * upgrades — cards you already own that the heuristic builder left out
  * acquisitions — cards you don't own that most decks with this commander run

Responses are cached on disk for a week; EDHREC data moves slowly and the site
deserves not to be hammered.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from card_data import name_key as _name_key
from collection import OwnedCard

CACHE_DIR = Path(".cache/edhrec")
CACHE_TTL = 7 * 86400

# "New Cards" is a recency feed rather than a recommendation, so it is skipped.
CATEGORIES = (
    "High Synergy Cards",
    "Game Changers",
    "Top Cards",
    "Creatures",
    "Instants",
    "Sorceries",
    "Enchantments",
    "Planeswalkers",
    "Utility Artifacts",
    "Mana Artifacts",
    "Utility Lands",
)

# Cards every deck runs; recommending them is noise.
_SKIP_NAMES = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
}


@dataclass
class Recommendation:
    name: str
    categories: list = field(default_factory=list)
    synergy: float = 0.0          # -1..1, how commander-specific the card is
    inclusion: float = 0.0        # 0..1, share of this commander's decks running it
    num_decks: int = 0
    owned: OwnedCard | None = None
    price_usd: float = 0.0        # cheapest printing, 0.0 when Scryfall has no price

    @property
    def category(self) -> str:
        return self.categories[0] if self.categories else ""

    @property
    def role(self) -> str:
        """How settled this card is in the archetype, from its inclusion rate.

        A staple is what the deck is expected to run; tech is a card a minority
        of pilots chose deliberately, which is where the interesting choices —
        and the ones worth arguing with — live.
        """
        if self.inclusion >= 0.60:
            return "Staple"
        if self.inclusion >= 0.25:
            return "Flex"
        return "Tech"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _cache_path(commander_name: str) -> Path:
    return CACHE_DIR / f"{_slug(commander_name)}.json"


def fetch_commander_cards(commander_name: str, refresh: bool = False) -> dict:
    """Return EDHREC's card sections for a commander, cached on disk for a week."""
    path = _cache_path(commander_name)
    if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # corrupt cache entry — refetch below

    from pyedhrec import EDHRec

    data = EDHRec().get_commander_cards(commander_name)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _merge_sections(sections: dict) -> dict:
    """Collapse EDHREC's per-type sections into one Recommendation per card name."""
    merged: dict[str, Recommendation] = {}
    for category in CATEGORIES:
        for entry in sections.get(category) or []:
            name = (entry.get("name") or "").strip()
            if not name or name.lower() in _SKIP_NAMES:
                continue
            potential = entry.get("potential_decks") or 0
            num_decks = entry.get("num_decks") or 0
            key = _name_key(name)
            rec = merged.get(key)
            if rec is None:
                rec = Recommendation(
                    name=name,
                    synergy=entry.get("synergy") or 0.0,
                    inclusion=(num_decks / potential) if potential else 0.0,
                    num_decks=num_decks,
                )
                merged[key] = rec
            rec.categories.append(category)
    return merged


def recommend(
    commander: OwnedCard,
    owned_cards: list,
    deck: list,
    limit: int = 20,
    refresh: bool = False,
    card_index: dict | None = None,
) -> dict:
    """Split EDHREC's recommendations for `commander` into upgrades and acquisitions.

    `card_index` is card_data.load_by_name(); pass it to price the cards you
    don't own, which is the whole point of the acquisition list.

    Returns {"upgrades": [...], "acquire": [...], "in_deck": int, "total": int},
    or {"error": "..."} if EDHREC could not be reached.
    """
    try:
        sections = fetch_commander_cards(commander.name, refresh=refresh)
    except Exception as exc:
        return {"error": f"EDHREC lookup failed: {exc}", "upgrades": [], "acquire": [],
                "in_deck": 0, "total": 0}

    merged = _merge_sections(sections)
    merged.pop(_name_key(commander.name), None)

    owned_by_key: dict[str, OwnedCard] = {}
    for card in owned_cards:
        if card.is_basic_filler:
            continue
        key = _name_key(card.name)
        # Prefer the printing that carries metadata (image, type line)
        if key not in owned_by_key or (not owned_by_key[key].type_line and card.type_line):
            owned_by_key[key] = card
    deck_keys = {_name_key(c.name) for c in deck}

    upgrades: list[Recommendation] = []
    acquire: list[Recommendation] = []
    in_deck = 0

    for key, rec in merged.items():
        if key in deck_keys:
            in_deck += 1
            continue
        card = owned_by_key.get(key)
        if card is not None:
            if set(card.color_identity).issubset(set(commander.color_identity)):
                rec.owned = card
                rec.price_usd = card.price_usd or 0.0
                upgrades.append(rec)
        else:
            if card_index is not None:
                data = card_index.get(key)
                rec.price_usd = (data.get("price_usd") or 0.0) if data is not None else 0.0
            acquire.append(rec)

    # Owned cards: lead with the ones most specific to this commander.
    upgrades.sort(key=lambda r: (r.synergy, r.inclusion), reverse=True)
    # Cards to buy: lead with the ones most decks run, i.e. the safest purchases.
    acquire.sort(key=lambda r: (r.inclusion, r.synergy), reverse=True)

    return {
        "upgrades": upgrades[:limit],
        "acquire": acquire[:limit],
        "in_deck": in_deck,
        "total": len(merged),
    }
