"""Bron: costacabana.nl — openbare huispagina's uit de sitemap.

Het zoek- en agenda-endpoint staat in robots.txt op 'Disallow' en wordt niet gebruikt.
"""
import html
import json
import re

from sources.common import cached, clean, get, keep, meters, text_features

SOURCE = "costacabana"
SITEMAP = "https://www.costacabana.nl/sitemap.xml"
PROP_RE = re.compile(r"/vakantiehuis/spanje/costa-brava/([a-z-]+)/([a-z0-9-]+)\.html$")

# Deze staan niet in de voorzieningentabel, dus die zoeken we in de beschrijving
TEXT_ONLY = {"jacuzzi", "lift", "sauna", "seaView", "games", "sound"}
DISTANCES = {
    "Dichtstbijzijnde strand": "beach",
    "Dichtstbijzijnde nachtleven / stadscentrum": "nightlife",
    "Dichtstbijzijnde supermarkt": "supermarket",
    "Dichtstbijzijnde restaurant": "restaurant",
    "Dichtstbijzijnde luchthaven": "airport",
}
SCORES = {
    "Privacy": "privacy",
    "Uitzicht": "view",
    "Comfort": "comfort",
    "Rustige omgeving": "quiet",
    "Sfeervol": "atmosphere",
    "Tuin grootte": "garden",
}


def ld_blocks(page):
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            yield json.loads(m.group(1))
        except json.JSONDecodeError:
            pass


def rows(page, tab, end):
    """Leest de th/td-tabel uit een tabblad van de huispagina; sterren worden een getal."""
    m = re.search(rf'id="{tab}">(.*?){end}', page, re.S)
    out = {}
    for k, v in re.findall(r"<th[^>]*>(.*?)</th>\s*<td>(.*?)</td>", m.group(1) if m else "", re.S):
        stars = re.search(r'title="([\d.]+) stars"', v)
        out[clean(k)] = float(stars.group(1)) if stars else clean(v)
    return out


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


def features(fac, text):
    yes = lambda k: str(fac.get(k, "")).startswith("Ja")
    airco = str(fac.get("Airconditioning", ""))
    seats = str(fac.get("Eettafel", ""))
    n_seats = re.search(r"\d+", seats)
    found = {
        "privatePool": "privé" in str(fac.get("Zwembad", "")),
        "poolPrivate": fac.get("Zwembad zichtbaar voor buren") == "Nee",
        "airco": airco.startswith("Ja"),
        "aircoFree": "inbegrepen" in airco,
        "bbq": yes("Barbecue"),
        "parking": yes("Eigen parkeerplaats") or yes("Garage"),
        "bigTable": "meer dan 10" in seats or bool(n_seats and int(n_seats.group()) >= 10),
        "dishwasher": yes("Vaatwasmachine"),
        "safe": yes("Kluis voor waardevolle spullen"),
    }
    found.update({k: True for k in text_features(text) if k in TEXT_ONLY})
    return sorted(k for k, v in found.items() if v)


def parse(page, town, slug, url):
    rental = faq = None
    for d in ld_blocks(page):
        if d.get("@type") == "VacationRental":
            rental = d
        elif d.get("@type") == "FAQPage":
            faq = d
    if not rental or "latitude" not in rental:
        return None  # offline huis: de pagina toont dan een algemeen overzicht

    place = rental.get("containsPlace", {})
    desc = rental.get("description", {}).get("@value", "")
    area = re.search(r"Gelegen in ([^,]+),", desc)
    name = rental.get("name", {}).get("@value", slug).replace(" - CostaCabana", "")

    answers = {q["name"]: q["acceptedAnswer"]["text"] for q in (faq or {}).get("mainEntity", [])}
    policy, min_age = group_policy(next((a for q, a in answers.items() if "jongeren" in q), ""))
    extra = re.search(r"toenemen tot (\d+)", next((a for q, a in answers.items() if "huisvesten" in q), ""))

    fac = rows(page, "facilities", 'id="distances"')
    dist = rows(page, "distances", "</table>")
    body = re.search(r'id="description">(.*?)id="facilities"', page, re.S)
    text = (clean(body.group(1)) if body else desc).lower()
    rating = rental.get("aggregateRating", {})
    persons = place.get("occupancy", {}).get("value")

    return {
        "id": f"costacabana-{rental.get('identifier') or slug}",
        "source": "CostaCabana",
        "name": html.unescape(name),
        "url": url,
        "town": town.replace("-", " ").title().replace(" De ", " de "),
        "area": re.sub(r"^de urbanisatie ", "", area.group(1).strip()) if area else "",
        "lat": rental["latitude"],
        "lng": rental["longitude"],
        "persons": persons,
        "personsMax": int(extra.group(1)) if extra else persons,
        "bedrooms": place.get("numberOfBedrooms"),
        "bathrooms": place.get("numberOfBathroomsTotal"),
        "beds": {b["typeOfBed"]: b["numberOfBeds"] for b in place.get("bed", [])},
        "m2": place.get("floorSize", {}).get("value"),
        "features": features(fac, text),
        "airco": str(fac.get("Airconditioning", "")),
        "seats": str(fac.get("Eettafel", "")),
        "dist": {key: meters(dist[label]) for label, key in DISTANCES.items() if meters(dist.get(label))},
        "scores": {key: fac[label] for label, key in SCORES.items() if isinstance(fac.get(label), float)},
        "groups": policy,
        "minAge": min_age,
        "rating": rating.get("ratingValue"),
        "reviews": rating.get("reviewCount", 0),
        "images": [re.sub(r"width=\d+,height=\d+", "width=640,height=360", i) for i in rental.get("image", [])[:6]],
        "luxury": False,
        "price": "",
    }


def load(refresh=False):
    urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", get(SITEMAP)) if PROP_RE.search(u)]
    houses = []
    for url in urls:
        town, slug = PROP_RE.search(url).groups()
        try:
            house = parse(cached(SOURCE, url, refresh, delay=0.4), town, slug, url)
        except Exception as e:  # één kapotte pagina mag de rest niet tegenhouden
            print(f"  waarschuwing: {url} overgeslagen ({e})")
            continue
        if house and keep(house):
            houses.append(house)
    return houses


if __name__ == "__main__":
    found = load()
    print(f"{len(found)} huizen")
    for h in found:
        print(f"  {h['name']:22} {h['town']:14} {h['persons']:>2}p {h['bedrooms']} slpk  {h['features']}")
