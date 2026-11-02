import re

# Current PRICE_PATTERN - requires € or eur or ht
PRICE_PATTERN = re.compile(
    r'(\d{1,3}(?:[\s]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(?:€|eur(?:os)?|ht)(?:\s*(?:/|par\s+)(?:m2|m²|h|heure|heures?|jour|jours?|ml|u|unit[eé]s?|mois))?(?=[,./\s]|$)',
    re.IGNORECASE,
)

test_lines = [
    "Sur les couloirs deux couches + pose moulures 1600 forfait",
    "peinture 2600",
    "Dans les WC pose de moulures 350",
    "pose carrelage 1600€ forfait",
    "pose 35€/m2",
]

for line in test_lines:
    m = PRICE_PATTERN.search(line)
    print(f"{'MATCH' if m else 'NOMATCH'}: {line[:50]} -> {m.group(0) if m else 'nothing'}")

