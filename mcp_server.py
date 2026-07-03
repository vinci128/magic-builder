"""MCP server exposing the Magic Commander deck builder as tools."""

import json
import sys
from pathlib import Path

# Ensure the project directory is on the path
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from arena_collection import load_owned_cards
from card_data import load_scryfall_lookup, enrich_collection
from commander import find_commanders
from deck_builder import build_deck, synergy_score
from standard_builder import build_standard_deck as _build_standard

mcp = FastMCP("magic-builder")

CSV_PATH = str(Path(__file__).parent / "ManaBox_Collection.csv")

# Cache enriched collection so repeated tool calls don't re-download
_cache: dict = {}


def _get_enriched_collection(collection_path: str) -> list:
    if collection_path in _cache:
        return _cache[collection_path]
    lookup, by_set_cn = load_scryfall_lookup()
    owned = load_owned_cards(collection_path, by_set_cn)
    enrich_collection(owned, lookup)
    _cache[collection_path] = owned
    return owned


@mcp.tool()
def list_commanders(csv_path: str = CSV_PATH) -> str:
    """List the top commander candidates from a collection file, scored by how many
    compatible cards exist in the collection. Accepts ManaBox CSV or Arena export format.

    Args:
        csv_path: Path to a ManaBox CSV or MTG Arena collection export file.

    Returns:
        JSON list of top commanders with name, color identity, oracle text, and score.
    """
    owned = _get_enriched_collection(csv_path)
    scored = find_commanders(owned)
    result = [
        {
            "rank": i + 1,
            "name": cmd.name,
            "color_identity": cmd.color_identity,
            "oracle_text": cmd.oracle_text,
            "compatible_cards": int(score),
        }
        for i, (cmd, score) in enumerate(scored[:15])
    ]
    return json.dumps(result, indent=2)


@mcp.tool()
def build_commander_deck(commander_name: str, csv_path: str = CSV_PATH) -> str:
    """Build a 99-card Commander deck using only cards from the collection.

    Fills slots by functional role (lands, ramp, card draw, removal) then synergy score.

    Args:
        commander_name: Exact name of the chosen commander (must be in the collection).
        csv_path: Path to a ManaBox CSV or MTG Arena collection export file.

    Returns:
        JSON object with commander details and the full 99-card list, each card annotated
        with its type, CMC, oracle text, color identity, and functional role.
    """
    owned = _get_enriched_collection(csv_path)

    # Find the commander card
    name_lower = commander_name.strip().lower()
    commander = next(
        (c for c in owned if c.name.lower() == name_lower and c.legalities.get("commander") == "legal"),
        None,
    )
    if commander is None:
        return json.dumps({"error": f"Commander '{commander_name}' not found in collection or not Commander-legal."})

    deck = build_deck(commander, owned)

    def card_dict(c, role: str = "") -> dict:
        return {
            "name": c.name,
            "type_line": c.type_line,
            "cmc": c.cmc,
            "color_identity": c.color_identity,
            "oracle_text": c.oracle_text,
            "keywords": c.keywords,
            "synergy_score": round(synergy_score(c, commander), 2),
            "role": role,
            "basic_filler": c.is_basic_filler,
        }

    # Annotate roles (mirror deck_builder logic labels)
    from deck_builder import _is_land, _is_ramp, _is_card_draw, _is_removal

    annotated = []
    for c in deck:
        if _is_land(c):
            role = "land"
        elif _is_ramp(c):
            role = "ramp"
        elif _is_card_draw(c):
            role = "card_draw"
        elif _is_removal(c):
            role = "removal"
        else:
            role = "synergy"
        annotated.append(card_dict(c, role))

    result = {
        "commander": card_dict(commander),
        "total_cards": len(deck) + 1,
        "deck": annotated,
        "summary": {
            "lands": sum(1 for c in annotated if c["role"] == "land"),
            "ramp": sum(1 for c in annotated if c["role"] == "ramp"),
            "card_draw": sum(1 for c in annotated if c["role"] == "card_draw"),
            "removal": sum(1 for c in annotated if c["role"] == "removal"),
            "synergy": sum(1 for c in annotated if c["role"] == "synergy"),
            "basic_fillers": sum(1 for c in annotated if c["basic_filler"]),
        },
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def build_standard_deck(csv_path: str = CSV_PATH, colors: str = "") -> str:
    """Build a 60-card Standard deck using only Standard-legal cards from the collection.

    Picks the strongest mono- or two-color identity (or a forced one), fills up to
    4 copies per card by rate + synergy under curve constraints, and adds a mana base.

    Args:
        csv_path: Path to a ManaBox CSV, Arena export, or Arena log-CSV collection file.
        colors: Optional forced colors, e.g. "W" or "UG". Empty = auto-pick strongest.

    Returns:
        JSON with chosen colors, the 60-card list (name, count, type, cmc, oracle text),
        and a curve/role summary.
    """
    owned = _get_enriched_collection(csv_path)
    color_set = set(colors.upper()) if colors.strip() else None
    deck, used_colors = _build_standard(owned, colors=color_set)

    cards = [
        {
            "count": e.count,
            "name": e.card.name,
            "type_line": e.card.type_line,
            "cmc": e.card.cmc,
            "mana_cost": e.card.mana_cost,
            "oracle_text": e.card.oracle_text,
        }
        for e in deck
    ]
    return json.dumps({
        "colors": sorted(used_colors),
        "total_cards": sum(e.count for e in deck),
        "lands": sum(e.count for e in deck if "Land" in e.card.type_line),
        "creatures": sum(e.count for e in deck if "Creature" in e.card.type_line),
        "deck": cards,
    }, indent=2)


@mcp.tool()
def get_collection_stats(csv_path: str = CSV_PATH) -> str:
    """Return high-level statistics about the card collection.

    Args:
        csv_path: Path to a ManaBox CSV or MTG Arena collection export file.

    Returns:
        JSON with total unique cards, color distribution, rarity breakdown, and
        the number of Commander-eligible legendary creatures.
    """
    owned = _get_enriched_collection(csv_path)

    color_counts: dict[str, int] = {}
    for c in owned:
        for col in c.color_identity:
            color_counts[col] = color_counts.get(col, 0) + 1
    color_counts["Colorless"] = sum(1 for c in owned if not c.color_identity)

    commanders_count = sum(
        1 for c in owned
        if "Legendary" in c.type_line
        and ("Creature" in c.type_line or "Planeswalker" in c.type_line)
        and c.legalities.get("commander") == "legal"
    )

    return json.dumps({
        "unique_cards": len(owned),
        "total_quantity": sum(c.quantity for c in owned),
        "commander_eligible_legends": commanders_count,
        "color_distribution": color_counts,
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
