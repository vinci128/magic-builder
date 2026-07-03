# Magic Deck Builder

Builds a 100-card Commander deck or a 60-card Standard deck from your collection using Scryfall card data, heuristic role-filling, and an optional Claude AI review pass. Also exposes its core functions as MCP server tools so Claude can call them directly and iterate on decks conversationally.

## Features

- Parses three collection formats, auto-detected: ManaBox CSV, Arena deck export (`N Card Name (SET) ###`), and Arena collection CSV scraped from Player.log (`grp_id,name,set,...`)
- Enriches every card with Scryfall data (type, oracle text, keywords, CMC, P/T, mana cost, rarity, legalities)
- **Standard mode** (`--format standard`): filters to Standard-legal cards, merges printings with a 4-copy cap, auto-picks the strongest mono- or two-color identity (rate + synergy-pair scoring: lifegain sources ↔ payoffs, tokens ↔ anthems), fills the curve with a guaranteed interaction floor, and builds a mana base from owned duals + weighted basics
- Scores and ranks commander candidates by collection coverage
- Fills heuristic role slots: lands, ramp, card draw, removal, synergy cards
- Synergy scoring tuned for ETB-heavy commanders (Chulane-style):
  - ETB bonus for commanders that draw on creature cast
  - Landfall bonus when commander drops lands from hand
  - Flash bonus for instant-speed creature play
  - Penalties for Ripple (useless in singleton), Equipment draw without Equipment, Angel synergy without Angels, self-mill for colorless mana
- Auto-distributes basic lands weighted by color demand of non-land cards
- Optional Claude AI review of the finished deck (requires `ANTHROPIC_API_KEY`)
- MCP server exposing three tools Claude can call directly

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

## Usage

```bash
# Build a deck (auto-selects best commander)
python main.py ManaBox_Collection.csv

# Pick a specific commander from the ranked list (e.g. pick #5)
python main.py ManaBox_Collection.csv --pick 5

# Save to a custom output file
python main.py ManaBox_Collection.csv --output my_deck.txt

# Skip the Claude AI review step
python main.py ManaBox_Collection.csv --no-ai

# Build a 60-card Standard deck from an Arena collection (auto-picks colors)
python main.py collection_from_logs.csv --format standard

# Force specific colors for the Standard deck
python main.py collection_from_logs.csv --format standard --colors WB
```

The Claude AI review requires an `ANTHROPIC_API_KEY` environment variable (or a `.env` file). Without it, use `--no-ai`.

Output is printed to the console and saved as:
- `deck_output.txt` — formatted deck list with sections
- `deck_output.decklist.txt` — plain `1 Card Name` import format for MTGO/Arena/Moxfield

## MCP Server

The builder exposes three tools via [FastMCP](https://github.com/jlowin/fastmcp) that Claude can call directly in a conversation:

| Tool | Description |
|------|-------------|
| `list_commanders` | Returns top 15 commander candidates with scores |
| `build_commander_deck` | Builds and returns the full 99-card deck as JSON |
| `build_standard_deck` | Builds a 60-card Standard deck (optionally forced colors) as JSON |
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
├── card_data.py         # Scryfall bulk data download + enrichment (.cache/)
├── commander.py         # Commander candidate scoring and selection
├── deck_builder.py      # 99-card Commander deck construction and synergy scoring
├── standard_builder.py  # 60-card constructed deck builder (Standard)
├── ai_advisor.py        # Claude API integration for deck review
├── output.py            # Console and file formatting
├── mcp_server.py        # FastMCP server exposing tools to Claude
└── requirements.txt
```

## Dependencies

- [anthropic](https://pypi.org/project/anthropic/) — Claude API client
- [click](https://pypi.org/project/click/) — CLI framework
- [requests](https://pypi.org/project/requests/) — Scryfall bulk data download
- [python-dotenv](https://pypi.org/project/python-dotenv/) — `.env` support
- [mcp](https://pypi.org/project/mcp/) + [fastmcp](https://pypi.org/project/fastmcp/) — MCP server
- [pyedhrec](https://pypi.org/project/pyedhrec/) — optional EDHREC popularity scores
