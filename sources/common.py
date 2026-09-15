"""Gedeelde helpers voor de bron-parsers.

Elke bron-module in sources/ heeft een functie `load(refresh=False) -> list[dict]`
die huizen teruggeeft in het formaat van HOUSE_FIELDS hieronder.
"""
import html
import math
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
LLORET = (41.6995, 2.8456)  # Plaça de la Vila
MAX_KM = 30                 # huizen verder weg laten we weg
MIN_PERSONS = 10            # kleinere huizen zijn niet interessant voor de groep

# Formaat van één huis (alles wat de kaart gebruikt). Onbekend = weglaten of lege waarde.
HOUSE_FIELDS = {
    "id": "str, uniek over bronnen heen, bijv. 'clubvillamar-maylea'",
    "source": "str, weergavenaam van de bron, bijv. 'Club Villamar'",
    "name": "str",
    "url": "str, huispagina bij de bron",
    "town": "str, bijv. 'Lloret de Mar'",
    "area": "str, wijk/urbanisatie of ''",
    "lat": "float", "lng": "float",
    "persons": "int, standaard max. personen",
    "personsMax": "int, incl. extra bedden (anders = persons)",
    "bedrooms": "int|None", "bathrooms": "float|None",
    "beds": "dict typeOfBed -> aantal, bijv. {'Double': 4, 'Single': 6} of {}",
    "m2": "int|None",
    "features": "list[str], subset van FEATURE_KEYS",
    "airco": "str, vrije tekst of ''",
    "seats": "str, eettafel-info of ''",
    "dist": "dict met meters: beach, nightlife, supermarket, restaurant, airport (alleen wat bekend is)",
    "scores": "dict, bijv. privacy/view 0-5 (alleen als de bron het heeft)",
    "groups": "'groepen ok' | 'gemengde groepen' | 'geen groepen' | 'onbekend'",
    "minAge": "int|None",
    "rating": "float|None (0-5)", "reviews": "int",
    "images": "list[str], max 6 absolute URLs",
    "luxury": "bool",
    "price": "str, bijv. 'vanaf €650 / nacht' of ''",
}

FEATURE_KEYS = [
    "privatePool", "poolPrivate", "airco", "aircoFree", "bbq", "parking", "bigTable",
    "games", "sound", "jacuzzi", "sauna", "lift", "seaView", "dishwasher", "safe",
]

# Trefwoorden (NL/EN/ES) om voorzieningen uit vrije tekst te halen
KEYWORDS = {
    "privatePool": r"private (swimming )?pool|privézwembad|prive zwembad|piscina privada",
    "airco": r"air ?con|airco|airconditioning|aire acondicionado",
    "bbq": r"barbecue|\bbbq\b|barbacoa",
    "parking": r"parking|parkeer|garage|aparcamiento",
    "dishwasher": r"dishwasher|vaatwas|lavavajillas",
    "safe": r"\bkluis\b|safe ?box|safety deposit|\bin-room safe",
    "jacuzzi": r"jacuzzi|bubbelbad|whirlpool|hot ?tub|spa bath",
    "lift": r"\blift\b|elevator|ascensor",
    "sauna": r"\bsauna",
    "seaView": r"sea ?views?|zeezicht|uitzicht op (de )?zee|vistas al mar|ocean view",
    "games": r"table tennis|ping ?pong|pool table|billiard|foosball|tafeltennis|pooltafel|biljart|tafelvoetbal",
    "sound": r"sound ?system|music system|\bhi-?fi\b|bluetooth speakers?|muziekinstallatie|geluidsinstallatie|\bsonos\b|disco-? ?(systeem|system|lights?|verlichting|bal|ball)",
}


def get(url):
    # curl i.p.v. urllib: de python.org-installatie op macOS mist vaak SSL-certificaten
    out = subprocess.run(["curl", "-sfL", "-A", UA, "--max-time", "40", url], capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace")


def cached(source, url, refresh=False, delay=0.5):
    """Haalt een URL op via cache/<source>/; wacht `delay` seconden na elke echte download."""
    folder = CACHE / source
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", url.split("://", 1)[-1])[-180:]
    f = folder / name
    if refresh or not f.exists() or f.stat().st_size == 0:
        f.write_text(get(url))
        time.sleep(delay)
    return f.read_text()


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def meters(value):
    m = re.search(r"([\d.,]+)\s*(km|m)\b", str(value))
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    return round(n * 1000 if m.group(2) == "km" else n)


def km_from_lloret(lat, lng):
    r = math.pi / 180
    d_lat, d_lng = (lat - LLORET[0]) * r, (lng - LLORET[1]) * r
    x = math.sin(d_lat / 2) ** 2 + math.cos(LLORET[0] * r) * math.cos(lat * r) * math.sin(d_lng / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


# Zinnen over de omgeving tellen niet mee: "het waterpark heeft glijbanen en jacuzzi's" zegt niets over het huis
SURROUNDINGS = (
    r"water ?park|aqua ?park|aquapark|water ?world|theme park|pretpark|parque acu|attraction|attractie"
    r"|\brides\b|water ?slides?|glijbaan|glijbanen|wave pools?|golfslagbad|spa hotel|wellness ?cent"
)


# "No lift", "geen airco", "sin ascensor": een ontkenning vlak ervoor telt niet als voorziening
NEGATION = r"\b(no|not|without|geen|niet|zonder|sin|sense)\b[^.,;!?]{0,20}$"


def text_features(text):
    sentences = re.split(r"(?<=[.!?;])\s+|\n", (text or "").lower())
    t = " ".join(s for s in sentences if not re.search(SURROUNDINGS, s))
    return sorted(
        k for k, p in KEYWORDS.items()
        if any(not re.search(NEGATION, t[max(0, m.start() - 30):m.start()]) for m in re.finditer(p, t))
    )


def keep(house):
    """Filter dat elke bron toepast: groot genoeg en binnen MAX_KM van Lloret."""
    return (house.get("persons") or 0) >= MIN_PERSONS and km_from_lloret(house["lat"], house["lng"]) <= MAX_KM
