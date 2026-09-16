"""Collection readers: ManaBox CSV, Arena exports and Arena log scrapes."""

from magic_builder.collection.arena import detect_collection_format, load_owned_cards
from magic_builder.collection.manabox import OwnedCard, parse_collection

__all__ = ["OwnedCard", "parse_collection", "load_owned_cards", "detect_collection_format"]
