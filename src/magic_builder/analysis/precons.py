"""Find the preconstructed Commander decks in a collection and say what to swap.

A precon is a known 100-card list, so the two questions a player asks about
one — "do I own it?" and "what should I change first?" — can both be answered
from the collection alone:

  * detection — EDHREC indexes every Commander precon with its set code and
    list. Any collection holding cards from a precon's set (or a ManaBox binder
    named after it) is checked against the list; owning nearly all of it means
    owning the deck.
  * swaps — candidates are the cards you own that are not in the precon and fit
    its colours. Each is scored on what EDHREC's upgrade guide says other
    players add, what the commander's own page says, whether it speaks the
    precon's mechanical language (the recurring words in its rules text), and
    whether it fills a role the precon is thin on. Cuts come from EDHREC's
    "cards to cut" list, with a cost-based fallback when that is unavailable.

Everything from EDHREC is cached on disk for a week, next to the commander
pages `data.edhrec` already caches. Detection still works offline once the
lists have been fetched once; a cold start needs the network.
"""

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace

import requests

from magic_builder.analysis import brackets, crispi
from magic_builder.builders.commander import (
    DRAW_TARGET,
    GENERIC_SYNERGY_THEMES,
    RAMP_TARGET,
    REMOVAL_TARGET,
    _is_basic_land,
    _is_card_draw,
    _is_land,
    _is_ramp,
    _is_removal,
    synergy_reasons,
)
from magic_builder.collection.manabox import OwnedCard
from magic_builder.data.edhrec import (
    CACHE_DIR,
    CACHE_TTL,
    _merge_sections,
    fetch_commander_cards,
)
from magic_builder.data.scryfall import enrich_collection, name_key

INDEX_URL = "https://json.edhrec.com/pages/precon.json"
PAGE_URL = "https://json.edhrec.com/pages/precon/{slug}.json"
_HEADERS = {"User-Agent": "magic_builder/1.0 (+https://github.com/vinci128/magic_builder)"}
_TIMEOUT = 20

BASIC_NAMES = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
}

# Own the list this completely and you own the deck. Precons ship with a few
# cards players pull straight away, and ManaBox exports sometimes miss a
# printing, so this is deliberately short of 100%.
MIN_COVERAGE = 0.90
# A binder named after the precon is strong evidence on its own; the list only
# has to be recognisably the same deck.
MIN_COVERAGE_WITH_BINDER = 0.50

# Rules-text patterns that mark a deck's mechanical identity. A pattern found in
# the rules text of at least THEME_SHARE of the precon's non-land cards is one
# of its themes, and a candidate card using it is speaking the deck's language.
# Reminder text is stripped first: every Treasure says "it's an artifact", and
# that is not an artifact theme.
THEME_PHRASES = (
    (r"\+1/\+1 counter", "+1/+1 counters"),
    (r"proliferate", "proliferate"),
    (r"\bequip(ment|ped)?\b", "Equipment"),
    (r"\baura\b", "Auras"),
    (r"\btreasure\b", "Treasure"),
    (r"\bfood\b", "Food"),
    (r"\bclue\b|investigate", "Clues"),
    (r"\btokens?\b", "tokens"),
    (r"from (your|a) graveyard (to|onto) the battlefield", "reanimation"),
    (r"from your graveyard", "graveyard recursion"),
    (r"\bmill\b", "self-mill"),
    (r"\bdiscard", "discard"),
    (r"\bsacrifice\b", "sacrifice"),
    (r"\bdies\b", "death triggers"),
    (r"\bartifacts?\b", "artifacts"),
    (r"\benchantments?\b", "enchantments"),
    (r"instant (and|or) sorcery", "spellslinger"),
    (r"landfall", "landfall"),
    (r"gain(s|ed)? .{0,12}life", "lifegain"),
    (r"ninjutsu", "ninjutsu"),
    (r"\bflying\b", "flyers"),
    (r"double strike", "double strike"),
    (r"power [4-7] or greater", "big power"),
    (r"\bvehicles?\b|\bcrew\b", "Vehicles"),
    (r"\blegendary\b|\bhistoric\b", "legends"),
    (r"(extra|additional) combat", "extra combats"),
)
THEME_SHARE = 0.12
COMMANDER_THEME_SHARE = 0.30
MAX_THEMES = 6
# Themes too broad to read off a commander's text: Cloud making two Treasures
# does not make Limit Break a tokens deck, and Terra gaining flying does not
# make Revival Trance a flyers deck. These need the card count to qualify.
UNBOOSTABLE = {"tokens", "flyers", "artifacts", "enchantments", "legends"}
# A creature type is a theme when the deck's *rules text* keeps naming it, not
# merely when many creatures happen to share it — the synergy_score blind spot
# in one sentence: Human Soldiers in a Human Soldier deck are not a theme.
TRIBAL_MENTIONS = 4

# A candidate has to clear this to be recommended at all. Anything lower is a
# card that merely fits the colours, and the precon already has 99 of those.
MIN_SWAP_SCORE = 4.0

# Buy-list price tiers (USD, Scryfall's cheapest printing): (floor, ceiling, label).
PRICE_TIERS = ((0.0, 1.0, "under $1"), (1.0, 3.0, "$1–3"), (3.0, 10.0, "$3–10"),
               (10.0, None, "$10 and up"))
# A card has to be in at least this share of decks — EDHREC's upgraded lists
# for the precon, or the commander's decks — to make the buy list.
MIN_ACQUIRE_INCLUSION = 0.08

_REMINDER_RE = re.compile(r"\([^)]*\)")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _rules(card: OwnedCard) -> str:
    """Oracle text without reminder text, lower-cased."""
    return _REMINDER_RE.sub("", card.oracle_text or "").lower()


# ── EDHREC fetches ───────────────────────────────────────────────────────────

def _cached_json(path, url: str, refresh: bool) -> dict:
    if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


@dataclass
class PreconInfo:
    slug: str
    name: str
    set_code: str
    series: str
    commanders: list
    image: str = ""


def fetch_index(refresh: bool = False) -> list:
    """Every precon EDHREC has an upgrade guide for, newest series first."""
    data = _cached_json(CACHE_DIR / "precon-index.json", INDEX_URL, refresh)
    infos: list[PreconInfo] = []
    for group in data.get("container", {}).get("json_dict", {}).get("cardlists", []):
        series = group.get("header", "")
        for view in group.get("cardviews", []):
            url = view.get("url") or ""
            slug = url.rsplit("/", 1)[-1]
            if not slug:
                continue
            # Partner precons list their commanders under "cards"; the rest put
            # the set on the view itself.
            faces = view.get("cards") or [view]
            commanders = [c.get("name", "") for c in faces if c.get("name")]
            set_code = (faces[0].get("set") or view.get("set") or "").lower()
            infos.append(PreconInfo(
                slug=slug,
                name=view.get("label") or view.get("name") or slug,
                set_code=set_code,
                series=series,
                commanders=commanders,
                image=view.get("precon_image", ""),
            ))
    return infos


@dataclass
class Precon:
    info: PreconInfo
    commanders: list                 # names, as EDHREC lists the deck's commander(s)
    cards: list                      # [(name, qty)] — the full 100, commanders included
    add: list = field(default_factory=list)        # [{"name", "inclusion"}], most-added first
    lands_add: list = field(default_factory=list)
    cut: list = field(default_factory=list)        # names, most-cut first
    lands_cut: list = field(default_factory=list)
    # How players actually helm it: [{"commanders": [names], "decks": n}], most
    # popular first. Partner precons are usually run as a pair EDHREC's own
    # "commander" field does not name.
    pairings: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.info.name

    def names(self) -> set:
        return {name_key(n) for n, _ in self.cards}


def fetch_precon(info: PreconInfo, refresh: bool = False) -> Precon:
    data = _cached_json(CACHE_DIR / f"precon-{info.slug}.json",
                        PAGE_URL.format(slug=info.slug), refresh)
    deck = data.get("deck", {})
    cards = []
    for section in (deck.get("cards") or {}).values():
        for entry in section:
            cards.append((entry[0], int(entry[1]) if len(entry) > 1 else 1))
    commanders = list(deck.get("commander") or info.commanders)
    # EDHREC files the commander under the deck, not in the card sections.
    listed = {name_key(n) for n, _ in cards}
    for name in commanders:
        if name_key(name) not in listed:
            cards.insert(0, (name, 1))

    lists = {}
    for cl in data.get("container", {}).get("json_dict", {}).get("cardlists", []):
        lists[cl.get("tag")] = cl.get("cardviews") or []

    def adds(tag):
        out = []
        for view in lists.get(tag, []):
            potential = view.get("potential_decks") or 0
            out.append({
                "name": view.get("name", ""),
                "inclusion": (view.get("num_decks") or 0) / potential if potential else 0.0,
                "synergy": view.get("synergy") or 0.0,
            })
        return out

    pairings = [
        {"commanders": [n.strip() for n in entry.get("value", "").split("//") if n.strip()],
         "decks": entry.get("count") or 0}
        for entry in data.get("precon_commander_counts") or []
    ]
    return Precon(
        info=info,
        commanders=commanders,
        cards=cards,
        add=adds("cardstoadd"),
        lands_add=adds("landstoadd"),
        cut=[v.get("name", "") for v in lists.get("cardstocut", [])],
        lands_cut=[v.get("name", "") for v in lists.get("landstocut", [])],
        pairings=[p for p in pairings if p["commanders"]],
    )


# ── Detection ────────────────────────────────────────────────────────────────

@dataclass
class Detected:
    precon: Precon
    coverage: float          # share of the precon's distinct non-basic names you own
    binder: str | None       # the ManaBox binder holding it, when there is one
    missing: list            # precon cards the collection lacks


def detect(owned: list, refresh: bool = False) -> list:
    """The precons this collection contains, best-covered first.

    Candidates are precons whose set code appears in the collection or whose
    name matches a ManaBox binder; each is then checked card by card.
    """
    index = fetch_index(refresh)
    owned_sets = {c.set_code for c in owned if c.set_code}
    owned_names = {name_key(c.name) for c in owned}
    binders: dict[str, str] = {}
    for card in owned:
        for binder in card.binders:
            binders.setdefault(_norm(binder), binder)

    found: list[Detected] = []
    for info in index:
        wanted = _norm(info.name)
        binder = next((orig for norm, orig in binders.items()
                       if len(wanted) >= 4 and wanted in norm), None)
        if binder is None and info.set_code not in owned_sets:
            continue
        try:
            precon = fetch_precon(info, refresh)
        except Exception:
            continue  # one unreachable page should not hide the others
        names = {k for k in precon.names() if k not in BASIC_NAMES}
        if not names:
            continue
        have = names & owned_names
        coverage = len(have) / len(names)
        floor = MIN_COVERAGE_WITH_BINDER if binder else MIN_COVERAGE
        if coverage >= floor:
            missing = sorted(n for n, _ in precon.cards
                             if name_key(n) in names - have)
            found.append(Detected(precon, coverage, binder, missing))
    found.sort(key=lambda d: -d.coverage)
    return found


# ── Swaps ────────────────────────────────────────────────────────────────────

def card_from_name(name: str, card_index: dict) -> OwnedCard | None:
    """An OwnedCard for a card you may not own, built from the Scryfall index."""
    data = card_index.get(name_key(name))
    if data is None:
        return None
    card = OwnedCard(name=data.name, scryfall_id=data.id, quantity=1, set_code=data.set)
    enrich_collection([card], {data.id: data})
    return card


def _subtypes(card: OwnedCard) -> set:
    # Front face only: a double-faced type line ("... — Human // Legendary
    # Enchantment Creature — Saga") would otherwise read "Creature" as a subtype.
    parts = card.type_line.split("//")[0].split("—")
    return set(parts[1].split()) if len(parts) > 1 else set()


def _role_view(card: OwnedCard):
    """`card` as builders.commander's role tests see it, with reminder text removed —
    a Lander token's reminder text otherwise reads as land ramp."""
    return SimpleNamespace(oracle_text=_rules(card), type_line=card.type_line)


def deck_themes(deck: list, commanders: list | None = None) -> list:
    """The precon's mechanical themes: [(pattern, label, share)], strongest first.

    Patterns come from THEME_PHRASES and are counted in rules text only;
    creature types qualify when the rules text names them repeatedly (payoffs),
    not when creatures merely carry them. A theme the commander's own text
    expresses is what the deck is *for*, however few cards serve it yet, so it
    is weighted as if it ran through COMMANDER_THEME_SHARE of the deck.
    """
    spells = [c for c in deck if not _is_land(c)]
    if not spells:
        return []
    texts = [_rules(c) for c in spells]
    commander_text = " ".join(_rules(c) for c in commanders or [])

    def weigh(rx, share, label):
        if label not in UNBOOSTABLE and rx.search(commander_text) and share >= THEME_SHARE / 2:
            return max(share, COMMANDER_THEME_SHARE)
        return share if share >= THEME_SHARE else 0.0

    found: dict[str, tuple[re.Pattern, float]] = {}
    for pattern, label in THEME_PHRASES:
        rx = re.compile(pattern)
        share = weigh(rx, sum(1 for t in texts if rx.search(t)) / len(spells), label)
        if share and (label not in found or share > found[label][1]):
            found[label] = (rx, share)

    types = Counter()
    for c in spells:
        if "Creature" in c.type_line:
            types.update(_subtypes(c))
    for subtype in types:
        if len(subtype) < 3:
            continue
        rx = re.compile(rf"\b{re.escape(subtype)}s?\b", re.IGNORECASE)
        mentions = sum(1 for t in texts if rx.search(t))
        if mentions >= TRIBAL_MENTIONS:
            label = f"{subtype} typal"
            found[label] = (rx, weigh(rx, mentions / len(spells), label) or mentions / len(spells))

    themes = [(rx, label, share) for label, (rx, share) in found.items()]
    themes.sort(key=lambda t: -t[2])
    return themes[:MAX_THEMES]


def _theme_hit(card: OwnedCard, rx: re.Pattern, label: str) -> bool:
    """Does the candidate speak this theme? Type line counts for what a card *is*
    (an Equipment, a Ninja), rules text for what it does."""
    if rx.search(_rules(card)):
        return True
    if label.endswith("typal"):
        return any(rx.fullmatch(s) for s in _subtypes(card))
    return label in ("Equipment", "Auras", "Vehicles", "artifacts", "enchantments") \
        and bool(rx.search(card.type_line.lower()))


def _land_colours(card: OwnedCard) -> set:
    colours = {c for c in "WUBRG" if f"{{{c}}}" in card.oracle_text}
    if re.search(r"any color|any one color", card.oracle_text, re.IGNORECASE):
        colours = set("WUBRG")
    for basic, colour in (("Plains", "W"), ("Island", "U"), ("Swamp", "B"),
                          ("Mountain", "R"), ("Forest", "G")):
        if basic in card.type_line:
            colours.add(colour)
    return colours


def _enters_tapped(card: OwnedCard) -> bool:
    t = card.oracle_text.lower()
    return "enters tapped" in t and "unless" not in t and "pay 2 life" not in t


@dataclass
class Swap:
    out: OwnedCard
    inn: OwnedCard
    score: float
    reasons: list
    out_reason: str = ""   # why this card is the one to cut


@dataclass
class Candidate:
    card: OwnedCard
    score: float
    reasons: list
    binders: list


def _score_candidate(card: OwnedCard, *, commanders: list, adds: dict,
                     page: dict, page_label: str, themes: list, needs: dict,
                     removal_avg_cmc: float, tapped_lands: int,
                     ci: set) -> tuple[float, list]:
    score = 0.0
    reasons: list[str] = []
    key = name_key(card.name)

    if key in adds:
        inclusion = adds[key]
        score += 4.0 + 6.0 * inclusion
        reasons.append(f"EDHREC: added to {inclusion:.0%} of upgraded lists")

    rec = page.get(key)
    if rec is not None and rec.inclusion >= 0.03:
        score += 1.5 + 4.0 * rec.inclusion + 3.0 * max(rec.synergy, 0.0)
        reasons.append(f"in {rec.inclusion:.0%} of {page_label} decks on EDHREC")

    if _is_land(card):
        colours = _land_colours(card) & ci
        if len(colours) >= 2 and not _enters_tapped(card) and tapped_lands >= 6:
            score += 2.5
            reasons.append(f"untapped {''.join(sorted(colours))} source — "
                           f"the precon has {tapped_lands} lands that enter tapped")
        return score, reasons

    # Themes: a card on the deck's strongest theme clears the bar by itself;
    # one on a minor theme needs a second reason. Further themes count for
    # less each, and a card that keeps coming back to the theme (Kíli names
    # Equipment three times) counts for more than one that mentions it once.
    rules = _rules(card)
    hits = sorted(((share, rx, label) for rx, label, share in themes
                   if _theme_hit(card, rx, label)), key=lambda h: -h[0])
    theme_points = 0.0
    for weight, (share, rx, label) in zip((1.0, 0.5, 0.25), hits):
        depth = min(max(len(rx.findall(rules)) - 1, 0) * 0.5, 1.0)
        theme_points += (10.0 * share + depth) * weight
        reasons.append(f"{label} — runs through {share:.0%} of the precon's rules text")
    score += min(theme_points, 6.0)

    # With partners, whichever commander the card talks to most.
    specific = max(
        ([r for r in synergy_reasons(card, c)
          if r.points > 0 and r.key not in GENERIC_SYNERGY_THEMES and r.key != "tribal"]
         for c in commanders),
        key=lambda rs: sum(r.points for r in rs))
    if specific:
        score += min(sum(r.points for r in specific), 3.0) * 0.5
        reasons.append(specific[0].label)

    # Roles the precon is short on, but only efficient versions: a 4-mana
    # sorcery that makes a Lander is not the ramp a precon is missing.
    view = _role_view(card)
    efficient = {
        "ramp": card.cmc <= 3,
        "card draw": card.cmc <= 3 and ("whenever" in rules or re.search(
            r"draw (two|three|four|x) cards", rules) is not None),
        "removal": card.cmc <= 3,
    }
    for role, (fn, have, target) in needs.items():
        if have < target and efficient[role] and fn(view):
            score += 1.5
            reasons.append(f"cheap {role}: the precon runs {have}, a build aims for {target}")
    if _is_removal(view) and card.cmc <= 2 and "Instant" in card.type_line \
            and "counter target" not in rules and removal_avg_cmc >= 3.0:
        score += 2.0
        reasons.append(f"{int(card.cmc)}-mana instant removal — the precon's answers "
                       f"average {removal_avg_cmc:.1f} mana")

    # Rarity and price are crude, but they separate a rare that does the same
    # thing from the common that also does it.
    quality = {"rare": 1.0, "mythic": 1.25}.get(card.rarity, 0.0)
    if card.price_usd >= 5:
        quality += 1.0
    elif card.price_usd >= 2:
        quality += 0.5
    if quality and reasons:
        score += quality
    # Precon upgrades lower the curve; a six-drop has to be vouched for.
    if key not in adds and card.cmc >= 6:
        score -= 1.0 if card.cmc < 8 else 2.0
    return score, reasons


def suggest_swaps(detected: Detected, owned: list, card_index: dict, *,
                  limit: int = 8, reserved_binders: set | None = None,
                  include_deck_cards: bool = False,
                  commander_names: list | None = None) -> dict:
    """What to take out of this precon and what of yours to put in.

    `reserved_binders` are the ManaBox binders holding your other decks (the
    other detected precons, typically); cards that live only there are left
    alone unless `include_deck_cards` is set, because pulling them breaks
    another deck.

    `commander_names` overrides who helms the deck — for partner precons the
    pairing you actually play changes both the themes the commanders' text
    expresses and which EDHREC page is consulted. Default: EDHREC's listing.
    """
    precon = detected.precon
    reserved = set(reserved_binders or ()) - ({detected.binder} if detected.binder else set())

    names = commander_names or precon.commanders
    commanders = [c for c in (card_from_name(n, card_index) for n in names) if c]
    if not commanders:
        return {"error": f"Could not resolve the commander of {precon.name}."}
    for card in commanders:
        if "Legendary" not in card.type_line:
            return {"error": f"{card.name} can't be a commander."}
    commander = commanders[0]
    ci = set()
    for c in commanders:
        ci |= set(c.color_identity)
    commander_keys = {name_key(c.name) for c in commanders}

    # The deck as it stands. A ManaBox binder is the truth about what is
    # sleeved — owners swap cards, and EDHREC's list can differ from the
    # printed one by a few cards — so it wins over the stock list.
    deck: list[OwnedCard] = []
    unresolved: list[str] = []
    if detected.binder:
        for card in owned:
            copies = card.binders.get(detected.binder, 0)
            if copies and name_key(card.name) not in commander_keys:
                deck.extend([card] * copies)
    else:
        for name, qty in precon.cards:
            if name_key(name) in commander_keys:
                continue
            card = card_from_name(name, card_index)
            if card is None:
                unresolved.append(name)
                continue
            deck.extend([card] * qty)

    themes = deck_themes(deck, commanders)
    spells = [c for c in deck if not _is_land(c)]
    views = [_role_view(c) for c in spells]
    removal = [c for c, v in zip(spells, views) if _is_removal(v)]
    needs = {
        "ramp": (_is_ramp, sum(1 for v in views if _is_ramp(v)), RAMP_TARGET),
        "card draw": (_is_card_draw, sum(1 for v in views if _is_card_draw(v)), DRAW_TARGET),
        "removal": (_is_removal, len(removal), REMOVAL_TARGET),
    }
    removal_avg = sum(c.cmc for c in removal) / len(removal) if removal else 0.0
    tapped = sum(1 for c in deck if _is_land(c) and _enters_tapped(c))

    adds = {name_key(a["name"]): a["inclusion"] for a in precon.add + precon.lands_add}
    # EDHREC keys a partner pair by both names, alphabetically.
    page_name = " ".join(sorted(c.name for c in commanders))
    try:
        page = _merge_sections(fetch_commander_cards(page_name))
    except Exception:
        page = {}

    deck_keys = {name_key(c.name) for c in deck} | commander_keys
    pool: dict[str, OwnedCard] = {}
    for card in owned:
        key = name_key(card.name)
        if key in deck_keys or card.is_basic_filler or _is_basic_land(card):
            continue
        if not set(card.color_identity) <= ci or card.legalities.get("commander") != "legal":
            continue
        # One entry per name, remembering every binder any printing sits in.
        pool.setdefault(key, []).append(card)

    page_label = " // ".join(c.name for c in commanders)
    candidates: list[Candidate] = []
    locked = 0
    for key, printings in pool.items():
        binders = sorted({b for c in printings for b in c.binders})
        card = max(printings, key=lambda c: bool(c.type_line))
        if binders and all(b in reserved for b in binders) and not include_deck_cards:
            locked += 1
            continue
        score, reasons = _score_candidate(
            card, commanders=commanders, adds=adds, page=page,
            page_label=page_label, themes=themes,
            needs=needs, removal_avg_cmc=removal_avg, tapped_lands=tapped, ci=ci)
        if score >= MIN_SWAP_SCORE and reasons:
            candidates.append(Candidate(card, round(score, 1), reasons, binders))
    candidates.sort(key=lambda c: (-c.score, -len(c.reasons), c.card.cmc, c.card.name))

    outs_spells, outs_lands = _cuts(precon, deck, page, page_label, commander_keys)

    swaps: list[Swap] = []
    leftover: list[Candidate] = []
    for cand in candidates:
        outs = outs_lands if _is_land(cand.card) else outs_spells
        if len(swaps) >= limit or not outs:
            leftover.append(cand)
            continue
        out, why = outs.pop(0)
        swaps.append(Swap(out, cand.card, cand.score, cand.reasons, why))

    after = [c for c in deck if name_key(c.name) not in {name_key(s.out.name) for s in swaps}]
    after += [s.inn for s in swaps]

    owned_keys = {name_key(c.name) for c in owned}
    acquire = _acquisitions(precon, page, card_index, owned_keys | deck_keys)

    return {
        "precon": precon,
        "commander": commander,
        "commanders": commanders,
        "deck": deck,
        "after": after,
        "themes": [{"label": label, "share": round(share, 2)} for _, label, share in themes],
        "swaps": swaps,
        "more": leftover[:8],
        "locked": locked,
        "unresolved": unresolved,
        "acquire": acquire,
        "before_eval": _evaluate(commander, deck),
        "after_eval": _evaluate(commander, after),
    }


def price_tier(price: float) -> str:
    for floor, ceiling, label in PRICE_TIERS:
        if price >= floor and (ceiling is None or price < ceiling):
            return label
    return PRICE_TIERS[-1][2]


def _acquisitions(precon: Precon, page: dict, card_index: dict, skip: set) -> list:
    """Cards you don't own that other players put in this deck, priced.

    Demand is the larger of two shares: how often the card is *added* to this
    precon (EDHREC's upgrade guide) and how often the commander's decks run it
    at all. Returned most-demanded first; group with `price_tier` to shop by
    budget.
    """
    adds = {name_key(a["name"]): a for a in precon.add + precon.lands_add}
    keys = set(adds) | {k for k, r in page.items() if r.inclusion >= MIN_ACQUIRE_INCLUSION}
    out = []
    for key in keys:
        if key in skip or key in BASIC_NAMES:
            continue
        data = card_index.get(key)
        if data is None or not data.price_usd:
            continue
        added = adds.get(key)
        rec = page.get(key)
        add_share = added["inclusion"] if added else 0.0
        run_share = rec.inclusion if rec else 0.0
        demand = max(add_share, run_share)
        if demand < MIN_ACQUIRE_INCLUSION:
            continue
        out.append({
            "name": data.name,
            "price": round(data.price_usd, 2),
            "tier": price_tier(data.price_usd),
            "added": round(add_share, 3),
            "run": round(run_share, 3),
            "demand": round(demand, 3),
            "synergy": round(rec.synergy if rec else (added["synergy"] if added else 0.0), 3),
            "type_line": data.type_line,
            "mana_cost": data.mana_cost or "",
            "image_url": data.image_url or "",
            "game_changer": data.get("game_changer") is True,
        })
    out.sort(key=lambda a: (-a["demand"], a["price"]))
    return out


# Below this share of the commander's decks, a precon card is one the people
# playing that commander have decided against.
CUT_INCLUSION = 0.35
# The commander page has to describe this much of the deck before its numbers
# outrank the precon's generic most-cut list.
CUT_PAGE_COVERAGE = 0.5


def _cuts(precon: Precon, deck: list, page: dict, page_label: str,
          commander_keys: set) -> tuple[list, list]:
    """Cards to take out, weakest first, as [(card, reason)], spells and lands apart.

    The commander page decides when it covers the deck: a precon card that few
    of this commander's decks keep is the cut, whatever the precon's generic
    most-cut list says — Tempestra is cut from most Turtle Power builds but
    kept by the Leonardo // Michelangelo ones, where she copies a commander.
    The generic list orders the rest, then mana value; lands follow EDHREC's
    lands-to-cut list, then whatever enters tapped for a single colour.
    """
    by_key = {}
    for card in deck:
        by_key.setdefault(name_key(card.name), card)
    generic = {name_key(n): i for i, n in enumerate(precon.cut)}
    spells = [c for c in by_key.values() if not _is_land(c)
              and name_key(c.name) not in commander_keys]
    covered = sum(1 for c in spells if name_key(c.name) in page) / len(spells) if spells else 0

    def keep_share(card):
        rec = page.get(name_key(card.name))
        # Absent from a page that covers the deck means too rare to be listed.
        return rec.inclusion if rec is not None else 0.0

    ranked_spells: list[tuple[OwnedCard, str]] = []
    if covered >= CUT_PAGE_COVERAGE:
        for card in sorted(spells, key=lambda c: (keep_share(c), generic.get(name_key(c.name), 99),
                                                  -c.cmc, c.name)):
            share = keep_share(card)
            if share >= CUT_INCLUSION and name_key(card.name) not in generic:
                continue
            why = (f"kept by {share:.0%} of {page_label} decks on EDHREC"
                   if name_key(card.name) in page
                   else f"too few {page_label} decks keep it to make EDHREC's list")
            ranked_spells.append((card, why))
    else:
        listed = sorted((c for c in spells if name_key(c.name) in generic),
                        key=lambda c: generic[name_key(c.name)])
        ranked_spells = [(c, "on EDHREC's most-cut list for this precon") for c in listed]
        rest = [c for c in spells if name_key(c.name) not in generic
                and keep_share(c) < CUT_INCLUSION]
        rest.sort(key=lambda c: (-c.cmc, c.name))
        ranked_spells += [(c, f"{int(c.cmc)} mana, and the commander's page doesn't vouch for it")
                          for c in rest]

    lands_cut = {name_key(n): i for i, n in enumerate(precon.lands_cut)}
    lands = [c for c in by_key.values() if _is_land(c) and not _is_basic_land(c)]
    listed = sorted((c for c in lands if name_key(c.name) in lands_cut),
                    key=lambda c: lands_cut[name_key(c.name)])
    ranked_lands = [(c, "on EDHREC's lands-to-cut list for this precon") for c in listed]
    rest = [c for c in lands if name_key(c.name) not in lands_cut and _enters_tapped(c)]
    rest.sort(key=lambda c: (len(_land_colours(c)), c.name))
    ranked_lands += [(c, "enters tapped") for c in rest]
    return ranked_spells, ranked_lands


def _evaluate(commander: OwnedCard, deck: list) -> dict:
    score = crispi.evaluate(commander, deck, None)
    bracket = brackets.evaluate(commander, deck, None, fmt="commander", crispi=score)
    return {
        "bracket": bracket["number"],
        "bracket_name": bracket["name"],
        "line": brackets.summary_line(bracket),
        "crispi": score["score"],
        "crispi_summary": score["summary"],
    }


# ── Output ───────────────────────────────────────────────────────────────────

def decklist_text(commanders: list, deck: list) -> str:
    """`N Card Name` lines, commanders first — what ManaBox and Moxfield import."""
    counts: Counter = Counter(c.name for c in deck)
    lines = [f"1 {c.name}" for c in commanders]
    spells = sorted((n for n in counts if not _is_land(next(c for c in deck if c.name == n))),
                    key=str.lower)
    lands = sorted((n for n in counts if n not in spells), key=str.lower)
    lines += [f"{counts[n]} {n}" for n in spells]
    lines += [f"{counts[n]} {n}" for n in lands]
    return "\n".join(lines) + "\n"


def format_report(results: list, per_tier: int = 8) -> str:
    """Text report for the CLI: one section per detected precon."""
    SEP = "═" * 64
    if not results:
        return "No preconstructed decks found in this collection."
    lines = []
    for det, result in results:
        precon = det.precon
        lines += [SEP, f"  {precon.name}  ({precon.info.series})", SEP]
        cover = f"{det.coverage:.0%} of the list owned"
        if det.binder:
            cover += f", binder “{det.binder}”"
        lines.append(cover)
        if det.missing:
            lines.append("in EDHREC's list but not in your copy: " + ", ".join(det.missing[:8])
                         + (" …" if len(det.missing) > 8 else ""))
        if result.get("error"):
            lines += [result["error"], ""]
            continue
        lines.append("commander: " + " // ".join(c.name for c in result["commanders"]))
        if result["themes"]:
            lines.append("themes: " + ", ".join(
                f"{t['label']} ({t['share']:.0%})" for t in result["themes"]))
        lines.append(f"stock: {result['before_eval']['line']}  ·  "
                     f"{result['before_eval']['crispi_summary']}")
        lines.append("")
        if not result["swaps"]:
            lines.append("Nothing you own clears the bar for this deck — what it has "
                         "is already the best fit in the collection.")
        for i, swap in enumerate(result["swaps"], 1):
            lines.append(f"{i:>2}. OUT  {swap.out.name}"
                         + (f"  — {swap.out_reason}" if swap.out_reason else ""))
            lines.append(f"    IN   {swap.inn.name}  [{swap.inn.mana_cost or 'land'}]  "
                         f"score {swap.score}")
            for reason in swap.reasons[:3]:
                lines.append(f"         · {reason}")
        if result["more"]:
            lines.append("")
            lines.append("also worth a look: " + ", ".join(c.card.name for c in result["more"]))
        if result["locked"]:
            lines.append(f"({result['locked']} candidate(s) skipped because they are sleeved "
                         f"in another of your decks — pass --include-deck-cards to consider them)")
        if result["swaps"]:
            lines.append("")
            lines.append(f"after swaps: {result['after_eval']['line']}  ·  "
                         f"{result['after_eval']['crispi_summary']}")
        if result.get("acquire"):
            lines.append("")
            lines.append("worth buying (Scryfall's cheapest printing, USD; % = share of "
                         "upgraded lists that add it / of the commander's decks that run it):")
            for _, _, label in PRICE_TIERS:
                tier = [a for a in result["acquire"] if a["tier"] == label][:per_tier]
                if not tier:
                    continue
                lines.append(f"  {label}")
                for a in tier:
                    flag = "  [Game Changer]" if a["game_changer"] else ""
                    lines.append(f"    {a['name']:<38} ${a['price']:>6.2f}   "
                                 f"added {a['added']:>3.0%} · run {a['run']:>3.0%}{flag}")
        lines.append("")
    return "\n".join(lines)
