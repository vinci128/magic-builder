"""Build the committed test fixtures from the local Scryfall cache — offline, deterministic.

Why bother: a fixture of invented card names tests the parser and nothing else, and it cannot double as
the "try it without your own collection" example the README wants. Real names with real Scryfall ids do
both — the parser tests get genuine data, and a stranger can build an actual deck from the committed
file without owning a single card.

Two properties this file is careful about, because a fixture that drifts is worse than no fixture:

- **Offline.** It reads `.cache/scryfall_default_cards.jsonl.gz`, the bulk file the tool itself already
  downloads. No API calls, so no rate limits and no 429s when regenerating — and the data matches what
  the builders see at runtime.
- **Deterministic.** Printings are chosen by a stable sort key, so re-running produces byte-identical
  output. A fixture that changes on every run shows up as a spurious diff and trains people to ignore it.

The list is deliberately a small, plausible black-red deck: a commander, a manabase, ramp, removal,
recursion, and basics. It is not anybody's real collection and it is not a good deck — it is enough to
exercise every code path and to produce output.

Usage:  python tests/build_fixtures.py
Writes: tests/fixtures/{collection_manabox.csv, collection_arena.txt, collection_arena_log.csv,
                        scryfall_index.json}
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".cache" / "scryfall_default_cards.jsonl.gz"

# (name, quantity) — a coherent if unambitious black-red list.
CARDS: list[tuple[str, int]] = [
    ("Alesha, Who Laughs at Fate", 1),      # commander
    ("Kroxa, Titan of Death's Hunger", 1),  # second candidate
    ("Tinybones Joins Up", 1),
    ("Thwart the Grave", 1),
    ("Zombify", 1),
    ("Rise from the Grave", 1),
    ("Undead Butler", 1),
    ("Phyrexian Gargantua", 1),
    ("Solemn Simulacrum", 1),
    ("Phyrexian Arena", 1),
    ("Liliana's Triumph", 1),
    ("Deadly Dispute", 1),
    ("Infernal Grasp", 1),
    ("Go for the Throat", 1),
    ("Heartless Act", 1),
    ("Tragic Slip", 1),
    ("Blasphemous Act", 1),
    ("Lightning Strike", 1),
    ("Arcane Signet", 1),
    ("Mind Stone", 1),
    ("Coldsteel Heart", 1),
    ("Command Tower", 1),
    ("Dragonskull Summit", 1),
    ("Smoldering Marsh", 1),
    ("Temple of Malice", 1),
    ("Bloodfell Caves", 1),
    ("Fabled Passage", 1),
    ("Swamp", 12),   # basic padding: the builder should top a deck up with these
    ("Mountain", 8),
]

BASICS = {"Swamp", "Mountain", "Island", "Forest", "Plains"}
# Set types to avoid when a name has many printings: gold-bordered, un-sets, playtest and online-only
# printings all exist in the bulk data and would look nothing like a paper binder.
BAD_SET_TYPES = {"memorabilia", "token", "minigame", "funny", "alchemy", "vanguard", "promo"}


def _cn_number(c: dict) -> int:
    """The numeric part of a collector number, or a large sentinel. '123a' -> 123, '★' -> sentinel."""
    s = str(c.get("collector_number", "")).rstrip("a-z★")
    return int(s) if s.isdigit() else 9999


def pick_printing(printings: list[dict], want_basic: bool) -> dict:
    """One printing for a name, chosen the same way every run.

    Preference order: English paper and non-promo, then a real expansion over a supplementary set, then
    the lowest collector number, then set code and id to break any remaining tie. The trailing keys
    exist purely so the result is stable — `min` over a total order, never "the first one seen", because
    iteration order is not a promise.
    """
    def ok(c: dict) -> bool:
        if c.get("digital") or c.get("lang") != "en":
            return False
        return c.get("set_type") not in BAD_SET_TYPES

    pool = [c for c in printings if ok(c)] or printings
    if want_basic:
        # For basics prefer a plain expansion printing, so the fixture has a normal-looking land.
        pool = [c for c in pool if c.get("set_type") == "expansion"] or pool
    return min(pool, key=lambda c: (
        c.get("set_type") != "expansion",
        _cn_number(c),
        c.get("set", ""),
        c.get("id", ""),
    ))


def main() -> None:
    if not CACHE.exists():
        sys.exit(f"no Scryfall cache at {CACHE}\n"
                 f"  run the tool once (it downloads the bulk file), then re-run this")
    HERE.mkdir(parents=True, exist_ok=True)

    wanted = {name for name, _ in CARDS}
    by_name: dict[str, list[dict]] = {}
    with gzip.open(CACHE, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            if c.get("name") in wanted:
                by_name.setdefault(c["name"], []).append(c)

    fetched: list[tuple[dict, int]] = []
    for name, qty in CARDS:
        printings = by_name.get(name)
        if not printings:
            sys.exit(f"card not in the cache: {name!r} — fix the name, there is no API fallback here")
        d = pick_printing(printings, want_basic=name in BASICS)
        fetched.append((d, qty))
        print(f"  {name:<34} {d['set']:>5} {d['collector_number']!s:>4}  {d['id'][:8]}  "
              f"{d.get('set_type','')}")

    # 1. ManaBox CSV: the columns the parser reads, in the order a real export has them.
    with (HERE / "collection_manabox.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Set code", "Set name", "Collector number", "Foil", "Quantity",
                    "Scryfall ID", "Binder Name"])
        for d, qty in fetched:
            w.writerow([d["name"], d["set"].upper(), d["set_name"], d["collector_number"],
                        "normal", qty, d["id"], ""])

    # 2. Arena export: `N Card Name (SET) ###`, which is what Arena's "export" produces.
    with (HERE / "collection_arena.txt").open("w", encoding="utf-8") as f:
        for d, qty in fetched:
            f.write(f"{qty} {d['name']} ({d['set'].upper()}) {d['collector_number']}\n")

    # 3. Arena log CSV: the shape scripts/scrape_collection.py produces from Player.log.
    with (HERE / "collection_arena_log.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grp_id", "name", "set", "collector_number", "quantity"])
        for i, (d, qty) in enumerate(fetched, start=1):
            w.writerow([i, d["name"], d["set"], d["collector_number"], qty])

    # 4. The (set|collector_number) index the two Arena parsers need. In production this comes from the
    #    bulk file; the fixture carries only its own cards, which is what keeps the tests fast.
    index = {f"{d['set'].lower()}|{str(d['collector_number']).lower()}": d for d, _ in fetched}
    (HERE / "scryfall_index.json").write_text(
        json.dumps(index, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    # 5. What was emitted, as data. The test suite compares the fixtures against this, so a real
    #    collection dropped into tests/fixtures/ by accident contains names that are not here and fails
    #    CI — the net under the .gitignore negation above, where a name-based guard cannot help (the
    #    synthetic files are legitimately called collection*.csv too).
    (HERE / "expected_cards.json").write_text(
        json.dumps(sorted(d["name"] for d, _ in fetched), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")

    print(f"\n  {len(fetched)} distinct cards, {sum(q for _, q in fetched)} physical cards, "
          f"5 fixture files")


if __name__ == "__main__":
    main()
