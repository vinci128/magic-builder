#!/usr/bin/env python3
"""Convert an mtga-export collection.json into the Arena collection CSV this
project reads (`grp_id,name,set,collector_number,quantity,...`).

    mtga-export -o arena-export
    python3 scripts/arena_export_to_csv.py arena-export/collection.json collection_from_logs.csv

mtga-export (https://github.com/PBernaerts/mtga-export) reads the collection
from the running Arena client, so the result is the real collection. The
older scripts/scrape_collection.py took the union of deck lists from
Player.log instead and counted cards in imported decks as owned; do not use
its output for building.
"""

import csv
import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    src, dst = argv[1], argv[2]
    with open(src, encoding="utf-8") as f:
        cards = json.load(f)["cards"]
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grp_id", "name", "set", "collector_number", "quantity",
                    "color_identity", "types", "rarity"])
        for c in sorted(cards, key=lambda c: (c["name"], c["set_code"], c["collector_number"])):
            w.writerow([c["grp_id"], c["name"], c["set_code"], c["collector_number"],
                        c["count"], "".join(c.get("colors") or []), "", c.get("rarity", "")])
    print(f"{len(cards)} printings -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
