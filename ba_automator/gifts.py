"""Reviewed English relationship-gift names, with no runtime data downloads.

The 51 item IDs come from SchaleDB's English ``Category == "Favor"`` data:
https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/en/items.json
That repository is archived. Names were reviewed on 2026-09-24 against current
SchaleDB item pages, including https://schaledb.com/item/5025 and /5030.
The current /5015 title differs from the archived spelling; both are accepted.

These are English names for 35 regular, 13 luxury, and 3 special relationship
gifts. Event currencies, Valentine's collectibles, and gift containers are not
relationship gifts. Future gifts or localization changes need a reviewed update;
unknown names must not become gifts through broad keywords or fuzzy matching.
"""

CATALOG_REVIEWED_ON = "2026-09-24"

# Keep game IDs to make updates and source checks straightforward:
# https://schaledb.com/item/<id>
GIFT_NAMES_BY_ID = {
    # Regular gifts.
    5000: "Wavecat Pillow",
    5001: "Peroro Wheel",
    5002: "A-Pods Pro",
    5003: "Forbidden Love: Its Beauty Lies in Its Illicitness",
    5004: "Hitgirls Gaming Magazine",
    5005: "Cherry-Rose Lip Gloss",
    5006: "Glassy Glow BB Cream",
    5007: "Military Camo Foundation Trio",
    5008: "Fine Cookie Set",
    5009: "Guns, Charm, and Zeal",
    5010: "Astronomical Telescope",
    5011: "Gamegirl Color Replica",
    5012: "MX-Ration C-Type Dessert Flavor",
    5013: "Matcha Ramune",
    5014: "Jellies Cushion",
    5015: "Extraordinary Cottontail Detective: Case of the Misty Hot Spring Slide",
    5016: "Ring Bit",
    5017: "Buried Treasure Map",
    5018: "Cosplayer Coke-Bottle Glasses",
    5019: "World's Most Useless Gadget",
    5020: "Teddy Bear with Bow",
    5021: "Movie Ticket",
    5022: "30-Color Paint Set",
    5023: "Classical Poetry Anthology",
    5024: "Cute Dishware Set",
    5025: "Brain Teaser Puzzle Cube",
    5026: "Large Whole Cake",
    5027: "Luxury Buffet Ticket",
    5028: "Potted Bug-Eating Plant",
    5029: "Extravagant Gilded Jar of Greed",
    5030: "Wind-Up Music Box",
    5031: "Embroidered Handkerchief",
    5032: "Encyclopedia",
    5033: "Health Food Supplement",
    5034: "Summer Floaty",
    # Luxury gifts.
    5100: "Lace Pillow",
    5101: "Streets of Thugs Vol. 1",
    5102: 'Samuela "The Beyond"',
    5103: "Antique Egg Handicraft",
    5104: "Mille-Feuille Traditional Parfait",
    5105: "Airbook Rare",
    5106: "Sophisticated Hairbrush",
    5107: "Nutrient-Packed Multivitamin Gummies",
    5108: "Finest Maple Bonsai",
    5109: "Sewing Kit",
    5110: "Music Concert Ticket",
    5111: "Three Times a Day Dumbbell Set",
    5112: 'Board Game "The Life"',
    # Special gifts, including the limited collaboration photo card.
    5997: "Beautiful Bouquet",
    5998: "Hatsune Miku Photo Card",
    5999: "Shiny Bouquet",
}
GIFT_NAMES = frozenset(GIFT_NAMES_BY_ID.values())

# Explicit source-backed historical spelling, not a general punctuation rule.
GIFT_ALIASES = frozenset({
    "Extraordinary Cottontail Detective- Case of the Misty Hot Spring Slide -",
})

_TYPOGRAPHY = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u02bc": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
})


def _normalized(name: str) -> str:
    return " ".join(name.translate(_TYPOGRAPHY).casefold().split())


_GIFT_LOOKUP = frozenset(_normalized(name) for name in GIFT_NAMES | GIFT_ALIASES)


def is_gift(name: str) -> bool:
    """Match a reviewed gift name, tolerating only case, spacing and typography."""
    return isinstance(name, str) and _normalized(name) in _GIFT_LOOKUP
