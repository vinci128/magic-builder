# CLAUDE.md

Deck builder for Commander, Brawl, Standard and Pauper from a card collection. Heuristic, no LLM at build time. README.md is the full reference; this file is what you need to work on it safely.

## Running

- Install: `uv venv .venv && uv pip install -e .` — the venv has **no pip module**, always go through `uv pip`.
- Entry points: `.venv/bin/magic-builder` (CLI), `magic-builder-web` (FastAPI, `PORT=8017` to move it), `magic-builder-mcp`. `python -m magic_builder` also works.
- Lint: `uv tool run ruff check src scripts`. Config is in pyproject. The baseline has ~20 pre-existing findings (BLE001, B008, …); fix only what you touch, don't churn the rest.
- Cache is cwd-relative `.cache/` (`MAGIC_BUILDER_CACHE` to move it). First run downloads Scryfall's ~80 MB bulk file; it is refreshed weekly.
- Web UI smoke test: start it, poll `GET /api/status` until `card_db_ready` is true, `POST /api/collection` (returns `id`), then `POST /api/collection/{id}/deck/commander` with `{}`. The deck comes back under `categories`, not `deck`.

## There is no test suite

No tests, no CI (`.github/` only holds `FUNDING.yml`). What actually catches builder bugs is a sweep over many real builds asserting invariants. After touching `builders/commander.py`, build across every available collection × {commander, brawl} × target_bracket {None, 1, 2, 3, 4} (~160 builds, about a minute) and assert on each:

- `len(deck) == formats.get(fmt).deck_size - 1`
- no duplicate names among non-basic cards — the pool holds one entry per *printing*, so one name can appear twice and slip past a name-keyed guard
- no dead tutors: `is_tutor(c) and not is_live_tutor(c, [commander] + deck)`
- no Game Changers when targeting bracket 1 or 2

After touching `analysis/`, re-score the saved decklists in `decks/` and check the CRISPI floors don't suddenly promote everything to bracket 3 — a scoring change that silently inflates brackets is the failure mode that matters.

## Layout rules

- Code is the `src/magic_builder` package: `collection/` (parsers), `data/` (scryfall, edhrec, scryfall_query), `builders/` (formats, archetype, candidates = commander picking, commander = the 99-card builder, standard), `analysis/` (brackets, crispi, combos, precons), `web/`, plus `cli.py`, `output.py`, `mcp_server.py`, `paths.py`.
- Subpackage `__init__.py` files must stay empty (except `collection/`): `builders` ↔ `analysis` is a package-level import cycle that only works because nothing is imported at package init.
- `scripts/` is stdlib-only and runs with system `python3`, not the venv.

## Collections are personal data — never commit them

`ManaBox_Collection*.csv`, `collection*.csv` and `arena-export/` are gitignored. This is a public repo and the history has already been purged once with `git filter-repo`; don't add exceptions, don't `git add -f`, and check `git status --ignored` before a commit that touches new data files.

## Arena collection

The only trustworthy Arena source is [mtga-export](https://github.com/PBernaerts/mtga-export) → `scripts/arena_export_to_csv.py` → `collection_from_logs.csv`. `scripts/scrape_collection.py` is superseded: it took the union of deck lists from `Player.log` and counted unowned cards from imported decks as owned. Decks built from its output (`decks/korvold_brawl.decklist.txt`) contain cards that were never in the collection. Don't build from it and don't resurrect the systemd unit that ran it.

Arena's set codes differ from Scryfall's in a few places (`DAR` → `dom`); the alias table is `_ARENA_SET_ALIASES` in `collection/arena.py`. Arena's deck importer rejects full double-faced names (`Front // Back`) — decklists must carry the front-face name only.

## Known weaknesses of the builders

- `synergy_score` in `builders/commander.py` is keyword-driven and has a recurring blind spot: a strong generic card loses its slot to filler that happens to match a word in the commander's text (Kroxa pulled in Giant's Boulder and Goblin Firebomb on the word "sacrifice"). When a build looks wrong, that's the first place to look; the fix pattern that has worked is a reserved role slot, not more keywords.
- `builders/candidates.py` ranks Brawl/Commander candidates by how many owned cards fit their colours, so it always proposes five-colour legends over a stronger narrow commander. `--pick N` walks that list; to build for a specific commander call `build_deck` directly.
- `card_power` / `_is_removal` heuristics miss planeswalkers and some removal wording ("destroy target nonartifact creature"). The Standard builder's colour auto-pick can prefer a pile of 1-ofs on tapped duals over a mono-colour deck with 4-of density.

When the user wants "the strongest deck", check the builder's output against the actual pool rather than trusting it; the saved lists in `decks/` were hand-built and then validated against owned quantities, legality and colour identity.

## Deployment

Render deploys the branch named in `render.yaml` (`feat/web-ui-edhrec`), not `main` or `dev`. Pushing `dev` does not touch the live site.

## Commits

Small, single-purpose commits with a one-line summary and a body that says why. Deck list changes go in their own `decks:` commits.
