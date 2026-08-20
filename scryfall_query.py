"""A small, deliberately partial reader for Scryfall search syntax.

Commander Spellbook describes some combo pieces as a *template* — "a persist
creature", "an artifact with mana value 0" — rather than a named card, and
hands over the Scryfall query that defines it. Its bracket endpoint then counts
those combos as present whether or not the deck can actually field the template,
which is how a deck with no persist creature gets told it has a two-card combo.

Answering "does this deck contain a card matching that query?" needs the query
evaluated, and only against the ~100 cards of one deck — the whole of Scryfall
is never searched here, so a compiled predicate is all this module returns.

The syntax is only partly modelled, and that is the point: `otag:` and friends
need Scryfall's tagger data we do not hold, and a query that guesses is worse
than one that abstains. `compile_query` returns None for anything it cannot
read exactly, and callers treat that as "unknown", not as "no match".
"""

import re

# Query keys this module can answer, mapped to the card field they read.
_TEXT_KEYS = {"t": "type_line", "type": "type_line",
              "o": "oracle_text", "oracle": "oracle_text"}
_KEYWORD_KEYS = frozenset(("kw", "keyword"))
_NUMERIC_KEYS = {"mv": "cmc", "cmc": "cmc", "manavalue": "cmc",
                 "pow": "power", "power": "power",
                 "tou": "toughness", "toughness": "toughness"}
_MANA_KEYS = frozenset(("m", "mana"))
_COLOR_KEYS = frozenset(("c", "color", "colors", "id", "identity", "ci"))

_ATOM_RE = re.compile(r"^(?P<key>[a-z]+)(?P<op>[:=<>]=?|!=)(?P<value>.*)$", re.I)
_MANA_SYMBOL_RE = re.compile(r"\{[^}]*\}")
_COLOR_LETTERS = frozenset("wubrgc")


class _Unsupported(Exception):
    """The query uses syntax this module does not model."""


# ── Card field readers ───────────────────────────────────────────────────────

def _lower(card, field: str) -> str:
    return (getattr(card, field, "") or "").lower()


def _number(card, field: str):
    """A card's numeric field, or None when it has none (`*`, `X`, missing)."""
    raw = getattr(card, field, None)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _mana_symbols(card) -> list:
    return [s.lower() for s in _MANA_SYMBOL_RE.findall(getattr(card, "mana_cost", "") or "")]


def _colors(card) -> set:
    return {c.lower() for c in (getattr(card, "color_identity", None) or [])}


# ── Atoms ────────────────────────────────────────────────────────────────────

_COMPARE = {
    ":": lambda a, b: a == b, "=": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
}


def _numeric_atom(field: str, op: str, value: str):
    try:
        wanted = float(value)
    except ValueError:
        raise _Unsupported(f"non-numeric comparison {value!r}")
    compare = _COMPARE[op]

    def test(card):
        have = _number(card, field)
        return have is not None and compare(have, wanted)
    return test


def _text_atom(field: str, op: str, value: str):
    # `t:creature` and `o:"enters tapped"` are both substring tests; the
    # equality forms Scryfall accepts here mean the same thing in practice.
    if op not in (":", "="):
        raise _Unsupported(f"operator {op!r} on a text field")
    if value.startswith("/"):
        raise _Unsupported("regex")
    needle = value.lower()

    def test(card):
        haystack = _lower(card, field)
        # Scryfall's `~` stands for the card's own name, which is how the
        # oracle text of the printed card actually reads.
        return (needle.replace("~", _lower(card, "name")) in haystack
                or needle.replace("~", "this creature") in haystack
                or needle.replace("~", "this card") in haystack)
    return test


def _keyword_atom(op: str, value: str):
    if op not in (":", "="):
        raise _Unsupported(f"operator {op!r} on a keyword")
    wanted = value.lower()

    def test(card):
        return any(wanted == k.lower() for k in (getattr(card, "keywords", None) or []))
    return test


def _mana_atom(op: str, value: str):
    """`mana={1}{G}` is an exact cost; `mana:{G}` asks the cost to contain one."""
    if op not in (":", "="):
        raise _Unsupported(f"operator {op!r} on a mana cost")
    wanted = [s.lower() for s in _MANA_SYMBOL_RE.findall(value)]
    if not wanted or "".join(wanted) != value.lower():
        raise _Unsupported(f"mana cost {value!r}")
    exact = op == "="

    def test(card):
        have = _mana_symbols(card)
        if exact:
            return sorted(have) == sorted(wanted)
        rest = list(have)
        for symbol in wanted:
            if symbol not in rest:
                return False
            rest.remove(symbol)
        return True
    return test


def _color_atom(op: str, value: str):
    """Only `=` is modelled — the subset forms need Scryfall's colour algebra."""
    if op not in (":", "="):
        raise _Unsupported(f"operator {op!r} on colours")
    letters = value.lower()
    if not letters or set(letters) - _COLOR_LETTERS:
        raise _Unsupported(f"colour {value!r}")
    wanted = set() if letters == "c" else set(letters)

    def test(card):
        return _colors(card) == wanted
    return test


def _atom(token: str):
    if token.startswith("!"):     # `!"Thraben Gargoyle"` — an exact name
        wanted = token[1:].strip('"\'').lower()
        return lambda card: _lower(card, "name") == wanted

    match = _ATOM_RE.match(token)
    if not match:
        raise _Unsupported(f"bare term {token!r}")
    key, op, value = match.group("key").lower(), match.group("op"), match.group("value")
    value = value.strip('"\'')
    if not value:
        raise _Unsupported(f"empty value in {token!r}")

    if key in _TEXT_KEYS:
        return _text_atom(_TEXT_KEYS[key], op, value)
    if key in _KEYWORD_KEYS:
        return _keyword_atom(op, value)
    if key in _NUMERIC_KEYS:
        return _numeric_atom(_NUMERIC_KEYS[key], op, value)
    if key in _MANA_KEYS:
        return _mana_atom(op, value)
    if key in _COLOR_KEYS:
        return _color_atom(op, value)
    raise _Unsupported(f"key {key!r}")


# ── Tokenizer and parser ─────────────────────────────────────────────────────

def _tokenize(query: str) -> list:
    tokens, buf, quote = [], "", ""
    for ch in query:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch in "()":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch.isspace():
            if buf:
                tokens.append(buf)
                buf = ""
        else:
            buf += ch
    if quote:
        raise _Unsupported("unbalanced quote")
    if buf:
        tokens.append(buf)
    return tokens


class _Parser:
    """Recursive descent over Scryfall's `or` / implicit-`and` / `-` grammar."""

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse(self):
        test = self.parse_or()
        if self.pos != len(self.tokens):
            raise _Unsupported(f"trailing {self.peek()!r}")
        return test

    def parse_or(self):
        terms = [self.parse_and()]
        while (self.peek() or "").lower() == "or":
            self.pos += 1
            terms.append(self.parse_and())
        if len(terms) == 1:
            return terms[0]
        return lambda card: any(t(card) for t in terms)

    def parse_and(self):
        terms = []
        while True:
            token = self.peek()
            if token is None or token == ")" or token.lower() == "or":
                break
            if token.lower() == "and":
                self.pos += 1
                continue
            terms.append(self.parse_unary())
        if not terms:
            raise _Unsupported("empty group")
        if len(terms) == 1:
            return terms[0]
        return lambda card: all(t(card) for t in terms)

    def parse_unary(self):
        token = self.peek()
        if token.lower() == "not" or token == "-":
            self.pos += 1
            inner = self.parse_unary()
            return lambda card: not inner(card)
        if token.startswith("-") and len(token) > 1:
            self.tokens[self.pos] = token[1:]
            inner = self.parse_unary()
            return lambda card: not inner(card)
        return self.parse_primary()

    def parse_primary(self):
        token = self.peek()
        if token == "(":
            self.pos += 1
            inner = self.parse_or()
            if self.peek() != ")":
                raise _Unsupported("unbalanced parenthesis")
            self.pos += 1
            return inner
        if token == ")":
            raise _Unsupported("unbalanced parenthesis")
        self.pos += 1
        return _atom(token)


def compile_query(query: str):
    """A predicate over cards, or None when the query is not fully modelled.

    None means "cannot say", never "matches nothing" — see the module docstring.
    """
    if not query or not query.strip():
        return None
    try:
        return _Parser(_tokenize(query)).parse()
    except (_Unsupported, IndexError, KeyError, re.error):
        return None
