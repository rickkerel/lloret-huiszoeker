#!/usr/bin/env python3
"""Haalt alle vakantiehuizen van costacabana.nl op en schrijft data.js voor de kaart.

Gebruik:  python3 build.py            (gebruikt cache/ waar mogelijk)
          python3 build.py --refresh  (alles opnieuw downloaden)

Alleen openbare huispagina's uit de sitemap worden opgehaald; de zoek- en
agenda-endpoints staan in robots.txt op 'Disallow' en worden dus niet gebruikt.
"""
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
SITEMAP = "https://www.costacabana.nl/sitemap.xml"
UA = "Mozilla/5.0 (lloret-huiszoeker; persoonlijk gebruik)"
PROP_RE = re.compile(r"/vakantiehuis/spanje/costa-brava/([a-z-]+)/([a-z0-9-]+)\.html$")


def get(url):
    # curl i.p.v. urllib: de python.org-installatie op macOS mist vaak SSL-certificaten
    out = subprocess.run(["curl", "-sfL", "-A", UA, "--max-time", "30", url], capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace")


def ld_blocks(page):
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            yield json.loads(m.group(1))
        except json.JSONDecodeError:
            pass


def group_policy(text):
    if not text:
        return "onbekend", None
    t = text.lower()
    age = re.search(r"minimumleeftijd van (\d+)", t)
    age = int(age.group(1)) if age else None
    if "niet geboekt worden door groepen" in t:
        return "geen groepen", age
    if "gemengde samenstelling" in t:
        return "gemengde groepen", age
    if "groepen" in t:
        return "groepen ok", age
    return "onbekend", age


def parse(page, town, slug, url):
    rental = faq = None
    for d in ld_blocks(page):
        if d.get("@type") == "VacationRental":
            rental = d
        elif d.get("@type") == "FAQPage":
            faq = d
    if not rental or "latitude" not in rental:
        return None

    place = rental.get("containsPlace", {})
    amen = {a["name"]: a["value"] for a in rental.get("amenityFeature", [])}
    desc = rental.get("description", {}).get("@value", "")
    area = re.search(r"Gelegen in ([^,]+),", desc)
    name = rental.get("name", {}).get("@value", slug).replace(" - CostaCabana", "")

    answers = {}
    for q in (faq or {}).get("mainEntity", []):
        answers[q["name"]] = q["acceptedAnswer"]["text"]
    groups_txt = next((a for q, a in answers.items() if "jongeren" in q), "")
    policy, min_age = group_policy(groups_txt)
    cap_txt = next((a for q, a in answers.items() if "huisvesten" in q), "")
    extra = re.search(r"toenemen tot (\d+)", cap_txt)

    beds = {b["typeOfBed"]: b["numberOfBeds"] for b in place.get("bed", [])}
    images = [re.sub(r"width=\d+,height=\d+", "width=640,height=360", i) for i in rental.get("image", [])[:6]]
    rating = rental.get("aggregateRating", {})
    persons = place.get("occupancy", {}).get("value")

    return {
        "id": rental.get("identifier"),
        "name": html.unescape(name),
        "type": rental.get("additionalType", ""),
        "url": url,
        "town": town.replace("-", " ").title().replace(" De ", " de "),
        "area": re.sub(r"^de urbanisatie ", "", area.group(1).strip()) if area else "",
        "lat": rental["latitude"],
        "lng": rental["longitude"],
        "persons": persons,
        "personsMax": int(extra.group(1)) if extra else persons,
        "bedrooms": place.get("numberOfBedrooms"),
        "bathrooms": place.get("numberOfBathroomsTotal"),
        "beds": beds,
        "m2": place.get("floorSize", {}).get("value"),
        "pool": bool(amen.get("pool")),
        "poolType": amen.get("poolType", ""),
        "pets": bool(rental.get("petsAllowed")),
        "aircoPaid": "tegen betaling" in answers.get(next((q for q in answers if "airconditioning" in q), ""), ""),
        "groups": policy,
        "minAge": min_age,
        "rating": rating.get("ratingValue"),
        "reviews": rating.get("reviewCount", 0),
        "images": images,
    }


def main():
    refresh = "--refresh" in sys.argv
    CACHE.mkdir(exist_ok=True)
    urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", get(SITEMAP)) if PROP_RE.search(u)]
    print(f"{len(urls)} huizen in sitemap")

    houses = []
    for i, url in enumerate(urls, 1):
        town, slug = PROP_RE.search(url).groups()
        f = CACHE / f"{town}__{slug}.html"
        if refresh or not f.exists() or f.stat().st_size == 0:
            print(f"  [{i}/{len(urls)}] download {slug}")
            f.write_text(get(url))
            time.sleep(0.4)
        h = parse(f.read_text(), town, slug, url)
        if h:
            houses.append(h)

    houses.sort(key=lambda h: (-(h["persons"] or 0), h["name"]))
    out = ROOT / "data.js"
    out.write_text(
        "// Gegenereerd door build.py op " + time.strftime("%Y-%m-%d %H:%M") + "\n"
        "window.HOUSES = " + json.dumps(houses, ensure_ascii=False, indent=1) + ";\n"
    )
    big = sum(1 for h in houses if (h["persons"] or 0) >= 12)
    print(f"{len(houses)} huizen geschreven naar data.js ({big} voor 12+ personen)")


if __name__ == "__main__":
    main()
