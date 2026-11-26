"""Tests for the collection parsers — the three formats, and what they do with rows they cannot use.

These exist because the repo had no tests at all, and the parsers are where a silent mistake costs the
most: a card quietly missing from the collection means the deck builder tells you that you do not own
something you are holding in your hand. Nothing crashes, the result is just wrong.

The fixtures are real cards with real Scryfall ids (see build_fixtures.py), and all three formats encode
the *same* collection. That is deliberate: the strongest test here is that the three parsers agree, which
catches format-specific bugs that a per-format test would happily encode as correct.

Run:  .venv/bin/python -m pytest tests/ -q
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from magic_builder.collection.arena import (
    detect_collection_format,
    load_owned_cards,
    parse_arena_collection,
    parse_arena_log_csv,
)
from magic_builder.collection.manabox import parse_collection

FIXTURES = Path(__file__).parent / "fixtures"
MANABOX = FIXTURES / "collection_manabox.csv"
ARENA = FIXTURES / "collection_arena.txt"
ARENA_LOG = FIXTURES / "collection_arena_log.csv"

# The fixture is 29 distinct cards holding 47 physical copies (the basics are multiples).
EXPECTED_CARDS = 29
EXPECTED_COPIES = 47


@pytest.fixture(scope="module")
def index() -> dict:
    """The (set|collector_number) map the Arena parsers need — in production, from the bulk file."""
    raw = json.loads((FIXTURES / "scryfall_index.json").read_text(encoding="utf-8"))
    return {tuple(k.split("|")): v for k, v in raw.items()}


def _total(cards) -> int:
    return sum(c.quantity for c in cards)


# ---------------------------------------------------------------- format detection

def test_detects_manabox():
    assert detect_collection_format(str(MANABOX)) == "manabox"


def test_detects_arena_export():
    assert detect_collection_format(str(ARENA)) == "arena"


def test_detects_arena_log():
    assert detect_collection_format(str(ARENA_LOG)) == "arena_log"


def test_manabox_detected_by_binder_column_alone(tmp_path):
    """A ManaBox export with no Scryfall ID column is still ManaBox — detection must not depend on it."""
    p = tmp_path / "x.csv"
    p.write_text("Name,Binder Name\nSol Ring,Box A\n", encoding="utf-8")
    assert detect_collection_format(str(p)) == "manabox"


def test_unknown_format_is_not_silently_treated_as_arena(tmp_path):
    """Detection falls back to 'arena' for anything unrecognised.

    That default is what makes a wrong file produce an empty collection and no error — the failure mode
    worth guarding. Either the fallback names itself, or load_owned_cards warns; this test pins the
    contract that an unparseable file does not quietly return nothing.
    """
    p = tmp_path / "weird.csv"
    p.write_text("alpha,beta\n1,2\n", encoding="utf-8")
    with pytest.warns(UserWarning):
        assert load_owned_cards(str(p), {}) == []


# ---------------------------------------------------------------- ManaBox

def test_manabox_reads_the_fixture():
    cards = parse_collection(str(MANABOX))
    assert len(cards) == EXPECTED_CARDS
    assert _total(cards) == EXPECTED_COPIES


def test_manabox_keeps_the_printing_details():
    card = next(c for c in parse_collection(str(MANABOX)) if c.name == "Command Tower")
    assert card.set_code == "eld"          # lowercased, as Scryfall spells it
    assert card.set_name == "Throne of Eldraine"  # as the fixture's export has it
    assert card.scryfall_id                     # a real id, not empty
    assert card.quantity == 1
    assert card.foil is False


def test_manabox_sums_duplicate_printings(tmp_path):
    """Two rows with the same Scryfall ID are one card with the quantities added.

    ManaBox emits a row per binder, so a card sleeved in two places arrives twice and must not become
    two cards — otherwise the deck builder thinks it owns two copies.
    """
    p = tmp_path / "dup.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Set code", "Set name", "Collector number", "Foil", "Quantity",
                    "Scryfall ID", "Binder Name"])
        w.writerow(["Sol Ring", "TRC", "Tales", "499", "normal", 1, "id-sol", "Box A"])
        w.writerow(["Sol Ring", "TRC", "Tales", "499", "normal", 2, "id-sol", "Deck 1"])
    cards = parse_collection(str(p))
    assert len(cards) == 1
    assert cards[0].quantity == 3
    assert cards[0].binders == {"Box A": 1, "Deck 1": 2}


def test_manabox_treats_foil_rows_as_foil(tmp_path):
    p = tmp_path / "foil.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        f.write("Name,Quantity,Scryfall ID,Foil\nSol Ring,1,abc,foil\n")
    assert parse_collection(str(p))[0].foil is True


def test_manabox_reports_rows_it_cannot_use(tmp_path, capsys):
    """A row with no Scryfall ID cannot be enriched, so it is dropped — but silently dropping it is the
    bug: the user never learns that part of their collection is missing.

    Reports on stdout, matching what the Arena parsers already do with unmatched cards.
    """
    p = tmp_path / "gap.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Quantity", "Scryfall ID"])
        w.writerow(["Sol Ring", 1, "id-ok"])
        w.writerow(["Mystery Card", 3, ""])
    cards = parse_collection(str(p))
    assert len(cards) == 1                       # the usable row survives
    out = capsys.readouterr().out
    assert "Mystery Card" in out                 # and the other is named, not swallowed
    assert "1" in out


# ---------------------------------------------------------------- Arena export

def test_arena_export_reads_the_fixture(index):
    cards = parse_arena_collection(str(ARENA), index)
    assert len(cards) == EXPECTED_CARDS
    assert _total(cards) == EXPECTED_COPIES


def test_arena_export_takes_the_canonical_name_from_scryfall(index):
    """The export says 'Alesha, Who Laughs at Fate (FDN) 115'; the card keeps Scryfall's own name, so a
    builder never has to trust the export's spelling."""
    card = next(c for c in parse_arena_collection(str(ARENA), index)
                if c.set_code == "fdn")
    assert card.name == "Alesha, Who Laughs at Fate"
    assert card.scryfall_id == index[("fdn", "115")]["id"]


def test_arena_export_reports_unmatched_cards(tmp_path, index, capsys):
    """A card the index does not know is reported with its set and number — the case that would
    otherwise be an invisible hole in the collection. The card that *is* in the index still survives:
    one bad line does not spoil the file."""
    p = tmp_path / "missing.txt"
    p.write_text("1 Nonexistent Card (ZZZ) 999\n1 Command Tower (ELD) 333\n", encoding="utf-8")
    cards = parse_arena_collection(str(p), index)
    out = capsys.readouterr().out
    assert "Nonexistent Card (ZZZ) 999" in out
    assert len(cards) == 1
    assert cards[0].name == "Command Tower"


def test_arena_export_ignores_blank_and_malformed_lines(tmp_path, index):
    """Blank lines and Arena section headers ('Deck', 'Sideboard') must not abort the parse."""
    p = tmp_path / "messy.txt"
    p.write_text("\nDeck\n1 Command Tower (ELD) 333\n\nSideboard\n1 Mind Stone (WTH) 153\n",
                 encoding="utf-8")
    assert len(parse_arena_collection(str(p), index)) == 2


# ---------------------------------------------------------------- Arena log CSV

def test_arena_log_reads_the_fixture(index):
    cards = parse_arena_log_csv(str(ARENA_LOG), index)
    assert len(cards) == EXPECTED_CARDS
    assert _total(cards) == EXPECTED_COPIES


def test_arena_log_translates_arena_set_codes(tmp_path, index):
    """Arena uses 'dar' and 'conf' where Scryfall has 'dom' and 'con'. Without the alias the card is
    silently unmatched, so the mapping has to be exercised rather than trusted."""
    p = tmp_path / "alias.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grp_id", "name", "set", "collector_number", "quantity"])
        w.writerow([1, "Aliased Card", "dar", "1", 1])
    aliased = {k: v for k, v in index.items()}
    aliased[("dom", "1")] = {"id": "id-aliased", "name": "Aliased Card", "set": "dom"}
    assert len(parse_arena_log_csv(str(p), aliased)) == 1


def test_arena_log_reports_rows_it_cannot_use(tmp_path, index, capsys):
    """A row with no set or no collector number is dropped — and reported, for the same reason as the
    ManaBox gap: the collection must not be quietly short."""
    p = tmp_path / "partial.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grp_id", "name", "set", "collector_number", "quantity"])
        w.writerow([1, "Command Tower", "eld", "333", 1])
        w.writerow([2, "Mystery Card", "", "", 2])
    cards = parse_arena_log_csv(str(p), index)
    assert len(cards) == 1
    out = capsys.readouterr().out
    assert "Mystery Card" in out


def test_arena_log_survives_a_junk_quantity(tmp_path, index):
    """A non-numeric quantity becomes 1 rather than raising: one bad cell should not lose the file."""
    p = tmp_path / "qty.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grp_id", "name", "set", "collector_number", "quantity"])
        w.writerow([1, "Command Tower", "eld", "333", "not a number"])
    cards = parse_arena_log_csv(str(p), index)
    assert cards[0].quantity == 1


# ---------------------------------------------------------------- the three agree

def test_all_three_formats_describe_the_same_collection(index):
    """The test worth having: three different inputs, one collection.

    All the fixtures encode the same cards, so the parsers must agree on how many cards there are, how
    many copies, and *which* cards. A per-format test passes happily while a format quietly drops a
    column it was not looking at; this one does not.
    """
    a = parse_collection(str(MANABOX))
    b = parse_arena_collection(str(ARENA), index)
    c = parse_arena_log_csv(str(ARENA_LOG), index)

    for label, cards in (("manabox", a), ("arena", b), ("arena_log", c)):
        assert len(cards) == EXPECTED_CARDS, f"{label} found {len(cards)} cards"
        assert _total(cards) == EXPECTED_COPIES, f"{label} found {_total(cards)} copies"

    assert {x.name for x in a} == {x.name for x in b} == {x.name for x in c}
    assert {x.scryfall_id for x in a} == {x.scryfall_id for x in b} == {x.scryfall_id for x in c}


def test_load_owned_cards_picks_the_right_parser(index):
    """The dispatcher must route each fixture to its own parser — this is the entry point the CLI uses."""
    for path in (MANABOX, ARENA, ARENA_LOG):
        cards = load_owned_cards(str(path), index)
        assert len(cards) == EXPECTED_CARDS, f"{path.name} -> {len(cards)} cards"


def test_detection_skips_leading_blank_lines(tmp_path, index):
    """A hand-edited export often starts with a blank line; that must not make the file unrecognisable."""
    ara = tmp_path / "leading.txt"
    ara.write_text("\n\n1 Command Tower (ELD) 333\n", encoding="utf-8")
    assert detect_collection_format(str(ara)) == "arena"
    assert len(load_owned_cards(str(ara), index)) == 1

    mb = tmp_path / "leading.csv"
    mb.write_text("\nName,Quantity,Scryfall ID\nSol Ring,1,abc\n", encoding="utf-8")
    assert detect_collection_format(str(mb)) == "manabox"


# ---------------------------------------------------------------- the fixtures stay synthetic

# .gitignore protects real collections by name, but tests/fixtures/ is a negation of it — so a real export
# dropped in that directory *would* be committed. A name-based guard cannot help, because the synthetic
# files are legitimately called collection*.csv as well. What can: the fixtures must contain exactly the
# cards build_fixtures.py declared, and nothing else. A real binder has other cards, so it fails.
MAX_FIXTURE_CARDS = 200

# The only files allowed in the fixtures directory. An allowlist rather than "everything except …"
# because the thing being prevented is an *extra* file — a real export someone copied in — and a check
# that only inspects the files it already knows about cannot see one.
ALLOWED_FIXTURE_FILES = {
    "collection_manabox.csv",
    "collection_arena.txt",
    "collection_arena_log.csv",
    "scryfall_index.json",
    "expected_cards.json",
}


def test_fixtures_directory_holds_nothing_unexpected():
    """Anything else in tests/fixtures/ is committed to a public repo, so nothing else goes there.

    This is the guard that would have missed nothing: adding a fourth file is the failure mode, and
    inspecting only the three known ones cannot see it.
    """
    present = {p.name for p in FIXTURES.iterdir() if p.name != "__pycache__"}
    extra = present - ALLOWED_FIXTURE_FILES
    assert not extra, (
        f"unexpected file(s) in tests/fixtures/: {sorted(extra)} — these are committed, so a real "
        f"collection must not be left here. Delete it, or add it to build_fixtures.py on purpose."
    )


def test_fixtures_contain_exactly_the_synthetic_cards():
    """The net under the .gitignore negation: fixtures must be provably not somebody's collection.

    Compares what each fixture parses to against the declared list, in both directions — a real export
    brings extra names, and a fixture that lost cards brings fewer.
    """
    expected = set(json.loads((FIXTURES / "expected_cards.json").read_text(encoding="utf-8")))
    assert len(expected) == EXPECTED_CARDS

    index_raw = json.loads((FIXTURES / "scryfall_index.json").read_text(encoding="utf-8"))
    index = {tuple(k.split("|")): v for k, v in index_raw.items()}
    parsed = {
        "collection_manabox.csv": parse_collection(str(MANABOX)),
        "collection_arena.txt": parse_arena_collection(str(ARENA), index),
        "collection_arena_log.csv": parse_arena_log_csv(str(ARENA_LOG), index),
    }
    for name, cards in parsed.items():
        got = {c.name for c in cards}
        assert got == expected, (
            f"{name} holds {len(got - expected)} card(s) that are not in the synthetic list "
            f"(e.g. {sorted(got - expected)[:3]}) and is missing {sorted(expected - got)[:3]} — "
            f"a real collection must not be committed here"
        )
        assert len(cards) <= MAX_FIXTURE_CARDS


def test_fixture_card_counts_are_exactly_what_we_committed():
    """A pinned count, so a regeneration that silently changes the fixture is visible.

    Not pedantry: the point of the fixture is that it is the same everywhere, and a number that drifts
    quietly is how a fixture stops meaning anything.
    """
    assert len(parse_collection(str(MANABOX))) == EXPECTED_CARDS
    assert _total(parse_collection(str(MANABOX))) == EXPECTED_COPIES
