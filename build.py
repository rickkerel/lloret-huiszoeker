#!/usr/bin/env python3
"""Verzamelt villa's uit alle bronnen in sources/ en schrijft data.js voor de kaart.

Gebruik:  python3 build.py                     (gebruikt cache/ waar mogelijk)
          python3 build.py --refresh           (alles opnieuw downloaden)
          python3 build.py costacabana villanovo   (alleen deze bronnen opnieuw inlezen)

Mislukt een bron, dan gebruiken we de laatst gelukte uitkomst uit cache/<bron>.json.
"""
import importlib
import json
import math
import re
import sys
import time
from pathlib import Path

from sources.common import CACHE

ROOT = Path(__file__).parent
SOURCES = ["costacabana", "clubvillamar", "excellence", "villanovo"]


def load_source(name, refresh):
    snapshot = CACHE / f"{name}.json"
    try:
        houses = importlib.import_module(f"sources.{name}").load(refresh)
        CACHE.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(houses, ensure_ascii=False))
        print(f"  {name}: {len(houses)} huizen")
        return houses
    except ModuleNotFoundError:
        print(f"  {name}: nog geen parser, overgeslagen")
    except Exception as e:
        print(f"  {name}: mislukt ({e})")
    if snapshot.exists():
        houses = json.loads(snapshot.read_text())
        print(f"  {name}: vorige versie gebruikt ({len(houses)} huizen)")
        return houses
    return []


def distance_m(a, b):
    r = math.pi / 180
    d_lat, d_lng = (b["lat"] - a["lat"]) * r, (b["lng"] - a["lng"]) * r
    x = math.sin(d_lat / 2) ** 2 + math.cos(a["lat"] * r) * math.cos(b["lat"] * r) * math.sin(d_lng / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(x))


def richness(h):
    return len(h.get("features", [])) + len(h.get("dist", {})) + len(h.get("scores", {})) + len(h.get("images", []))


def merge(houses):
    """Hetzelfde huis bij meerdere bureaus wordt één pin met meerdere aanbieders.

    Zelfde huis = binnen 150 m en hooguit 1 slaapkamer verschil, bij een andere bron.
    Het huis met de meeste gegevens wordt de basis.
    """
    merged = []
    for h in sorted(houses, key=richness, reverse=True):
        offer = {"source": h["source"], "url": h["url"], "price": h.get("price", "")}
        twin = next((m for m in merged
                     if h["source"] not in {o["source"] for o in m["offers"]}
                     and distance_m(m, h) < 150
                     and abs((m.get("bedrooms") or 0) - (h.get("bedrooms") or 0)) <= 1), None)
        if not twin:
            merged.append({**h, "offers": [offer]})
            continue
        twin["offers"].append(offer)
        twin["features"] = sorted(set(twin["features"]) | set(h.get("features", [])))
        twin["luxury"] = twin["luxury"] or h.get("luxury", False)
        twin["price"] = twin["price"] or h.get("price", "")
        twin["personsMax"] = max(twin["personsMax"] or 0, h.get("personsMax") or 0)
        twin["dist"] = {**h.get("dist", {}), **twin["dist"]}
        twin["images"] = twin["images"] or h.get("images", [])
    return merged


# Bronnen spellen plaatsnamen verschillend; zo komt elke plaats maar één keer in de dropdown
TOWN_NAMES = {
    "macanet de la selva": "Maçanet de la Selva",
    "fogars de la selva": "Fogars de la Selva",
    "playa d aro": "Platja d'Aro",
    "platja d aro": "Platja d'Aro",
    "sant feliu de guixols": "Sant Feliu de Guíxols",
    "sant antoni de calonge": "Sant Antoni de Calonge",
    "st antoni de calonge": "Sant Antoni de Calonge",
    "playa de aro": "Platja d'Aro",
    "tossa de mar": "Tossa de Mar",
    "lloret de mar": "Lloret de Mar",
}

DEFAULTS = {
    "area": "", "personsMax": None, "bedrooms": None, "bathrooms": None, "beds": {}, "m2": None,
    "features": [], "airco": "", "seats": "", "dist": {}, "scores": {}, "groups": "onbekend",
    "minAge": None, "rating": None, "reviews": 0, "images": [], "luxury": False, "price": "",
}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    refresh = "--refresh" in sys.argv
    print("Bronnen inlezen:")
    houses = []
    for name in SOURCES:
        if args and name not in args:
            snapshot = CACHE / f"{name}.json"
            houses += json.loads(snapshot.read_text()) if snapshot.exists() else []
            continue
        houses += load_source(name, refresh)

    houses = [{**DEFAULTS, **h} for h in houses]
    for h in houses:
        h["personsMax"] = h["personsMax"] or h["persons"]
        h["town"] = TOWN_NAMES.get(h["town"].lower(), h["town"])
        # Sommige bureaus plakken plaats/land achter de huisnaam ("Villa X Lloret de Mar 2", "Villa Y Spain")
        name = re.sub(rf"\s+{re.escape(h['town'])}(?=(\s+\d+)?$)", "", h["name"], flags=re.I)
        h["name"] = re.sub(r"\s+Spain$", "", name).strip(" -,") or h["name"]
    merged = merge(houses)
    merged.sort(key=lambda h: (-(h["persons"] or 0), h["name"]))

    (ROOT / "data.js").write_text(
        "// Gegenereerd door build.py op " + time.strftime("%Y-%m-%d %H:%M") + "\n"
        "window.HOUSES = " + json.dumps(merged, ensure_ascii=False, indent=1) + ";\n"
    )
    dupes = len(houses) - len(merged)
    big = sum(1 for h in merged if (h["persons"] or 0) >= 12)
    print(f"{len(merged)} huizen naar data.js ({big} voor 12+ personen, {dupes} dubbele samengevoegd)")


if __name__ == "__main__":
    main()
