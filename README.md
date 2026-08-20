# Magic Deck Builder

[![ko-fi](https://img.shields.io/badge/support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/vinci128)

Builds a Commander, Brawl, Standard or Pauper deck from your collection using Scryfall card data, heuristic role-filling, and EDHREC's real-world inclusion data. No LLM, no API key. Also exposes its core functions as MCP server tools so Claude can call them directly and iterate on decks conversationally.

## Features

- Parses three collection formats, auto-detected: ManaBox CSV, Arena deck export (`N Card Name (SET) ###`), and Arena collection CSV scraped from Player.log (`grp_id,name,set,...`)
- Enriches every card with Scryfall data (type, oracle text, keywords, CMC, P/T, mana cost, rarity, legalities, USD price)
- **Four formats** (`--format`), defined in `formats.py`: `commander` and `brawl` (1 commander + 99, singleton) and `standard` and `pauper` (60 cards, 4-of, plus a sideboard). Each is just a Scryfall legality key, a deck size, a copy cap and a sideboard size, so the two builders stay generic.
- **Constructed mode** (`--format standard` / `pauper`): merges printings with a 4-copy cap, auto-picks the strongest mono- or two-color identity (rate + synergy-pair scoring: lifegain sources ↔ payoffs, tokens ↔ anthems), fills the curve with a guaranteed interaction floor, builds a mana base from owned duals + weighted basics, then fills a 15-card sideboard from the leftovers, favouring answers the maindeck can't afford (artifact/enchantment removal, graveyard hate, counterspells)
- **Deck names** decks the way players do — colour identity plus the theme the builder's own scoring actually followed: "Orzhov Lifegain", "Golgari Elves", "Mono-Red Aggro". Falls back to a shape word (Aggro / Midrange / Control / Ramp) when no theme has enough cards behind it to claim.
- **Prices** from Scryfall on every card, deck and recommendation, using the cheapest printing of each name. The collection panel shows what the whole collection is worth; the acquisition list shows what the missing cards would cost.
- Scores and ranks commander candidates by collection coverage, optionally weighted by EDHREC deck counts (on by default in the CLI, a toggle in the web UI)
- Fills heuristic role slots: lands, ramp, card draw, removal, synergy cards
- Synergy scoring tuned for ETB-heavy commanders (Chulane-style):
  - ETB bonus for commanders that draw on creature cast
  - Landfall bonus when commander drops lands from hand
  - Flash bonus for instant-speed creature play
  - Penalties for Ripple (useless in singleton), Equipment draw without Equipment, Angel synergy without Angels, self-mill for colorless mana
- Auto-distributes basic lands weighted by color demand of non-land cards
- **Commander bracket (1-5)** estimated for every Commander and Brawl deck, with the evidence behind it — see below
- EDHREC recommendation pass on finished Commander decks: splits what other players run into **upgrades** (cards you own that the builder skipped) and **acquisitions** (cards you don't own that most decks with this commander run), each labelled Staple / Flex / Tech by inclusion rate, with a synergy score and a price. Cached on disk for a week.
- Web UI (`python web_app.py`): upload a collection, pick a format and a commander or colours, read the deck with its name, price, mana curve, pip demand, sideboard, EDHREC recommendations, and card art previews
- MCP server exposing four tools Claude can call directly

## Setup

```bash
# Clone the repo
git clone https://github.com/vinci128/magic-builder
cd magic-builder

# Create a virtual environment and install dependencies
uv venv .venv
uv pip install -r requirements.txt
```

Export your collection from ManaBox as a CSV and place it in the project directory.

On its first run the builder downloads Scryfall's bulk card database (~80 MB gzipped) into `.cache/` and refreshes it weekly. Everything after that is local except the EDHREC lookup.

## Automatic Arena collection scrape

`scrape_collection.py` regenerates `collection_from_logs.csv` from MTG Arena's `Player.log` (Steam/Proton install, app ID 2141910). Arena no longer logs the full collection, so the scraper takes the union of all deck contents (max quantity per card across decks — starter/precon cards count as owned) from the login account payload, and resolves card ids to name/set/collector number via Arena's own card database (`Raw_CardDatabase_*.mtga`). Requires **Detailed Logs (Plugin Support)** enabled in Arena's Account options.

It runs automatically: the `mtga-collection.path` systemd user unit (`~/.config/systemd/user/`) watches `Player.log` and triggers `mtga-collection.service` after each Arena session. Run manually with `python3 scrape_collection.py`; it only rewrites the CSV when the collection changed.

## Usage

```bash
# Build a deck (auto-selects best commander)
python main.py ManaBox_Collection.csv

# Pick a specific commander from the ranked list (e.g. pick #5)
python main.py ManaBox_Collection.csv --pick 5

# Save to a custom output file
python main.py ManaBox_Collection.csv --output my_deck.txt

# Skip the EDHREC lookup (fully offline once the card database is cached)
python main.py ManaBox_Collection.csv --no-recs

# Build to a Commander bracket: keeps out what the bracket disallows, and
# for bracket 3 puts in the Game Changers that are what reach it
python main.py ManaBox_Collection.csv --target-bracket 3

# Build a 60-card Standard deck from an Arena collection (auto-picks colors)
python main.py collection_from_logs.csv --format standard

# Force specific colors for the Standard deck
python main.py collection_from_logs.csv --format standard --colors WB

# Other formats: Brawl (100-card singleton, Arena pool) and Pauper (commons only)
python main.py collection_from_logs.csv --format brawl
python main.py collection_from_logs.csv --format pauper
```

The EDHREC pass runs on Commander decks only and needs no credentials. Responses are cached under `.cache/edhrec/` for a week; use `--no-recs` to skip the lookup entirely.

Output is printed to the console and saved as:
- `deck_output.txt` — formatted deck list with sections
- `deck_output.decklist.txt` — plain `1 Card Name` import format for MTGO/Arena/Moxfield

## Commander brackets

Every Commander and Brawl deck is scored against WotC's bracket scale — 1 Exhibition, 2 Core, 3 Upgraded, 4 Optimized, 5 cEDH — and reported with the cards that decided it. Each bracket pairs an expected game length (9+, 8+, 6+, 4+ turns, then any) with a list of what a deck may contain, so the panel shows every criterion and marks the ones that fired:

| Criterion | Source |
| --- | --- |
| Game Changers | Scryfall's `game_changer` field — WotC's published list, so this one is exact |
| Mass land denial | Rules-text detection (`brackets.py`), matching sweeps and standing locks but not spot land removal, one-off symmetric sacrifices, or wipes that spare lands |
| Extra turns | Rules-text detection, separating one-shot spells from permanents that repeat |
| Tutors | Rules-text detection, excluding land fetch — context only, see below |
| Two-card infinite combos | [Commander Spellbook](https://commanderspellbook.com), which classifies each combo as two-card and reports how early it can go off — filtered here against the deck's own contents, see below |
| Deck speed | Fast mana, free spells, cheap interaction and curve — this project's reading, advisory only |

Current as of WotC's **February 9, 2026** beta update. The turn expectations arrived in the October 21, 2025 update, which also **removed the tutor guiderails from every bracket** — the efficient tutors are Game Changers and already counted, so tutors are reported here as context and gate nothing. They still shape what gets *built*, though: see the tutor package under "Building to a bracket" below.

Three limits worth knowing:

- **Bracket 5 is never assigned.** Nothing in a decklist separates cEDH from Optimized — it is a claim about the metagame the deck was built for, which only its pilot can make.
- **Bracket 1 is never assigned either, and 2 is the floor.** Exhibition is a deck whose theme was chosen ahead of its power. Nearly every casual deck clears its restriction list without being one — every precon does — so a classifier that stops at the first list a deck satisfies calls every clean deck a 1. When a deck also clears bracket 1's stricter rules, that is offered as a note rather than a verdict. Moxfield and Archidekt land in the same place: their automatic detection effectively never assigns a 1.
- **Deck speed does not move the number.** The published restrictions decide the bracket; the speed read sits next to it, because a deck with fast mana and eight one-mana answers but no Game Changer is a 2 by the letter of the rules while being able to end a game well before the turn 8 that bracket asks for.

### CRISPI

Alongside the bracket, every Commander deck is scored on **CRISPI** — Consistency, Resilience, Interaction, Speed — reimplemented from [DeckCheck's published rubric](https://deckcheck.co/blog/crispi-deep-dive). Each attribute scores 1–10 in quarter-point steps and the CRISPI Score is their mean.

Its job here is the thing the card restrictions cannot see. A deck can obey every rule of bracket 2 and still end games on turn 4, so CRISPI adds **floors that raise a bracket, never lower it**:

| Floor | Trips on |
| --- | --- |
| Bracket 5 | Speed 9+ **or** CRISPI 8.5+ |
| Bracket 4 | Speed 8+ **or** CRISPI 7.0+ **or** (Consistency 7.5+ **and** Interaction 7.5+) |
| Bracket 3 | Speed 6+ **or** CRISPI 5.0+ |
| Bracket 2 | Speed 5+ **or** CRISPI 3.5+ |

**Two of the four attributes are estimated, not counted.** DeckCheck's own engine is deterministic for Consistency and Interaction and leaves two judgements to an AI: Speed's *fundamental turn* (the rubric wants a dozen goldfish hands played out) and Resilience's *commander dependency*. `crispi.py` has no AI at build time, so it estimates Speed from combo speed where Commander Spellbook found one and otherwise from curve, ramp and fast mana, and assumes the format-default "Moderate" commander dependency. Both carry an `estimated` flag the whole way to the UI, which draws their bars hatched rather than solid, and the CLI marks them `~`.

The scale also runs low against DeckCheck's: they recalibrated so precons centre near 5, and precons measured here land near 3.75, because their scoring leans on a hand-curated card database while this reads rules text. The error direction is the safe one — scoring low means the floors fire *less* often, so this under-promotes rather than over-promotes. Across the 15 top commanders in the sample collection, no deck is bumped; a deliberately fast, restriction-clean burn deck is correctly bumped from 2 to 3.

**Building to a bracket.** `--target-bracket N` on the CLI, or the target picker in the web UI's format panel, builds to a bracket instead of reporting one. It keeps out what that bracket disallows (Game Changers below 3, mass land denial and chained extra turns below 4) and seeds in the Game Changers that reach 3 and above — synergy scoring alone never picks them, since Farewell shares nothing with an Elemental commander. It also sizes the tutor package to the target: 8 slots at bracket 4 and 5, 3 at bracket 3 and by default, 2 at brackets 1 and 2. Bracket 4 means playing your best cards *every* game, which is a search question as much as a card-quality one — three tutors in a deck that owns Demonic Tutor is a good bracket 3 deck, and CRISPI's search ladder agrees: its Consistency column needs roughly eight live tutors to clear 8. Two-card combos depend on which pairs end up together and are only known once Spellbook has answered, so they are reported against the target rather than built around, and the CLI says so when they push a deck past what you asked for.

**Combos are filtered against the deck that was actually built.** Spellbook describes some combo pieces by *template* — "a persist creature", "an artifact with mana value 0" — rather than by name, and counts those combos as present whether or not your deck can field one. A Celes, Rune Knight deck holding no persist creature was told it had an early two-card combo, and read as a bracket 4 on the strength of it. Each template carries the Scryfall query that defines it, so `scryfall_query.py` compiles that query and runs it over the deck's ~100 cards; a combo whose template matches nothing is listed as what it is ("1 more needs a card this deck doesn't run") and gates nothing. The query reader is deliberately partial — `otag:` and friends need Scryfall's tagger data this project does not hold — and a query it cannot read exactly reads as *met*, so the filter only ever removes combos it can prove the deck cannot assemble.

The combo check is the only part that needs the network. The CLI skips it under `--no-recs`, and the web UI renders the bracket immediately from the local criteria and refines it when Spellbook answers. Either way an unchecked combo criterion is reported as unchecked rather than as clean, since a missed combo can only mean the real bracket is *higher*. Responses are cached under `.cache/combos/` for a week, keyed by deck contents.

## Web UI

A browser front end for the same builders: drop in a collection file, pick one of the four formats and a commander or a colour pair, get the named deck back with its paper price, mana curve, pip demand, sideboard, card art on hover, and copy/download buttons. Commander and Brawl decks then load a **What other players run** section — EDHREC's upgrades and acquisitions, each tagged Staple / Flex / Tech and priced.

```bash
python web_app.py             # http://127.0.0.1:8000
PORT=8010 python web_app.py   # pick another port
```

The Scryfall database loads once in the background at startup — the header shows when it's ready (first run downloads ~80 MB). One upload can feed several builds: switch format, change commander, rebuild without re-uploading.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/status` | Card database readiness |
| `POST /api/collection` | Upload a collection file, returns an id plus collection stats |
| `GET /api/collection/{id}/commanders` | Ranked commander candidates (`?edhrec=true` adds popularity weighting, `?fmt=` picks commander or brawl) |
| `POST /api/collection/{id}/deck/commander` | Build a 100-card singleton deck (`commander_name`, `fmt`: commander \| brawl, `target_bracket`: 1-5) |
| `POST /api/collection/{id}/deck/standard` | Build a 60-card deck plus sideboard (`colors`, `fmt`: standard \| pauper) |
| `GET /api/collection/{id}/recommendations` | EDHREC upgrades and acquisitions for a built deck (`commander`, `limit`, `refresh`) |

Collections live in memory only — nothing is written to disk, and the 20 most recent uploads are kept.

## Deployment

The web UI runs at **https://magic-builder.onrender.com**.

`render.yaml` deploys it to [Render](https://render.com) as a free-tier web
service. In the Render dashboard: **New → Blueprint**, point it at this repo,
and it reads the file.

```yaml
buildCommand: pip install -r requirements.txt
              python -c "from card_data import _download_bulk_data; _download_bulk_data()"
startCommand: uvicorn web_app:app --host 0.0.0.0 --port $PORT
```

The bulk card file is downloaded during the build rather than on first request,
so a cold start only pays for loading the cards, not the 80 MB download.

Two things to know about the free tier:

* **512 MB memory.** Loading all 116k cards costs about 116 MB (see the
  `CardView` note in `card_data.py` — as plain dicts it was 644 MB and would
  not fit), leaving room for uvicorn and a handful of uploaded collections.
* **Sleeps after 15 minutes idle.** The first request after a sleep waits
  ~1–2 minutes for the instance and the card database to come back up.

No `healthCheckPath` is configured, deliberately: with one set, Render's router
dropped the instance for roughly half of all requests
(`x-render-routing: no-server`) while the process itself stayed up.

## MCP Server

The builder exposes four tools via [FastMCP](https://github.com/jlowin/fastmcp) that Claude can call directly in a conversation:

| Tool | Description |
|------|-------------|
| `list_commanders` | Returns top 15 commander candidates with scores |
| `build_commander_deck` | Builds and returns the full 99-card deck as JSON |
| `build_standard_deck` | Builds a 60-card Standard or Pauper deck plus sideboard (optionally forced colors) as JSON |
| `get_collection_stats` | Returns collection size, color distribution, eligible legends |

To enable it in Claude Code, add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "magic-builder": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/mcp_server.py"]
    }
  }
}
```

## Project Structure

```
magic_builder/
├── main.py              # CLI entry point (click)
├── collection.py        # ManaBox CSV parser → OwnedCard objects
├── arena_collection.py  # Arena export + Player.log CSV parsers, format detection
├── scrape_collection.py # Player.log → collection_from_logs.csv (auto-run by systemd)
├── card_data.py         # Scryfall bulk data download + enrichment (.cache/*.jsonl.gz)
├── formats.py           # Format specs: legality key, deck size, copy cap, sideboard
├── archetype.py         # Colour-combo names (Azorius, Jund...) and deck naming
├── commander.py         # Commander candidate scoring and selection
├── brackets.py          # Commander bracket (1-5) estimation and its criteria
├── combos.py            # Two-card combo lookup via Commander Spellbook (.cache/combos/)
├── scryfall_query.py    # Partial Scryfall-syntax reader, for combo template requirements
├── deck_builder.py      # 99-card singleton deck construction and synergy scoring
├── standard_builder.py  # 60-card constructed deck builder + sideboard
├── edhrec_recs.py       # EDHREC upgrade / acquisition recommendations (.cache/edhrec/)
├── output.py            # Console and file formatting
├── mcp_server.py        # FastMCP server exposing tools to Claude
├── web_app.py           # FastAPI web UI + JSON API
├── web/                 # Front end (index.html, styles.css, app.js)
└── requirements.txt
```

## Dependencies

- [click](https://pypi.org/project/click/) — CLI framework
- [requests](https://pypi.org/project/requests/) — Scryfall bulk data download
- [mcp](https://pypi.org/project/mcp/) + [fastmcp](https://pypi.org/project/fastmcp/) — MCP server
- [pyedhrec](https://pypi.org/project/pyedhrec/) — EDHREC recommendations and popularity scores
- [fastapi](https://pypi.org/project/fastapi/) + [uvicorn](https://pypi.org/project/uvicorn/) + [python-multipart](https://pypi.org/project/python-multipart/) — web UI
