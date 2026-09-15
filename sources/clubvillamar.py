"""Club Villamar (https://www.clubvillamar.com).

Werkwijze:
1. sitemap.xml -> welke plaatsen (city-slugs) bestaan er rond Lloret de Mar.
2. Per plaats de zoekpagina met filter maxSleeps>=10 (JSON in de HTML: SearchFormBox.queryResult),
   gepagineerd per 18. Daarmee slaan we kleine huizen over zonder detailpagina.
3. Per kandidaat de detailpagina: schema.org ld+json (geo, occupancy, slaapkamers, amenities, prijs,
   rating) plus tekstsecties (bedden, afstanden, groepsbeleid, services).
"""
import json
import re

from sources.common import CACHE, FEATURE_KEYS, cached, clean, keep, meters, text_features

SOURCE = "clubvillamar"
BASE = "https://www.clubvillamar.com"
MAX_DETAIL_PAGES = 200

# Basis-slugs van plaatsen binnen ~30 km (varianten als 'macanet-de-la-selva-1' tellen ook mee)
TOWNS = {
    "lloret-de-mar", "tossa-de-mar", "blanes", "vidreres", "macanet-de-la-selva", "tordera", "sils",
    "caldes-de-malavella", "brunyola", "llagostera", "sant-feliu-de-guixols", "santa-cristina-de-aro",
    "santa-cristina-daro", "playa-d-aro", "platja-d-aro", "sagaro", "calella", "malgrat-de-mar",
    "pineda-de-mar", "fogars-de-la-selva", "santa-susanna", "palafolls", "hostalric", "massanes",
    "riudarenes", "cassa-de-la-selva", "castell-platja-d-aro",
}
REGIONS = {"costa-brava", "costa-maresme"}

stats = {"fetched": 0, "cached": 0}


def fetch(url, refresh):
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", url.split("://", 1)[-1])[-180:]
    f = CACHE / SOURCE / name
    stats["cached" if (f.exists() and f.stat().st_size and not refresh) else "fetched"] += 1
    return cached(SOURCE, url, refresh)


def component(page, name):
    m = re.search(r'data-component-name="%s"[^>]*>(.*?)</script>' % name, page, re.S)
    return json.loads(m.group(1)) if m else None


def town_slugs(refresh):
    xml = fetch(BASE + "/sitemap.xml", refresh)
    out = set()
    for url in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        parts = url.split("/villas/", 1)[-1].split("/") if "/villas/" in url else []
        if len(parts) == 4 and parts[0] == "spain" and parts[1] in REGIONS:
            if re.sub(r"-\d+$", "", parts[2]) in TOWNS:
                out.add((parts[1], parts[2]))
    return sorted(out)


def candidates(region, city, refresh):
    """Villa's met >= 10 slaapplaatsen via de zoekpagina (gepagineerd)."""
    found, page = {}, 1
    while page <= 20:
        prefix = f"{BASE}/search/" + (f"page/{page}/" if page > 1 else "")
        url = f"{prefix}maxSleeps/10/city/{city}/region/{region}/country/spain"
        try:
            data = component(fetch(url, refresh), "SearchFormBox") or {}
        except Exception as e:  # noqa: BLE001
            print(f"  waarschuwing clubvillamar: zoekpagina {url} mislukt: {e}")
            break
        q = data.get("queryResult") or {}
        villas = q.get("villas") or []
        mine = [v for v in villas if v.get("citySlug") == city]
        for v in mine:
            if (v.get("maxSleeps") or 0) >= 10:
                uri = (v.get("villaUri") or "").replace("http://", "https://")
                found[uri] = v
        # Onbekende/kleine plaats: de site valt terug op een algemene lijst -> niet verder bladeren
        if not mine or len(mine) < len(villas) or page * int(data.get("perpage") or 18) >= (q.get("totalResultsCount") or 0):
            break
        page += 1
    return found


def ld_nodes(page):
    nodes = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        for n in (d.get("@graph") or [d]) if isinstance(d, dict) else d:
            if isinstance(n, dict):
                nodes.append(n)
    return nodes


def section(text, start, ends):
    i = text.find(start)
    if i < 0:
        return ""
    j = min([k for k in (text.find(e, i + len(start)) for e in ends) if k > 0] or [i + 2000])
    return text[i + len(start):j].strip()


def parse(url, page, listing):
    nodes = ld_nodes(page)
    acc = next(n for n in nodes if "Accommodation" in (n.get("@type") if isinstance(n.get("@type"), list) else [n.get("@type")]))
    vr = next((n for n in nodes if n.get("@type") == "VacationRental"), {})
    text = clean(re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S))
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    name = acc.get("name") or listing.get("name") or slug

    geo = vr.get("geo") or acc.get("geo") or {}
    lat = float(geo.get("latitude") or listing["latitude"])
    lng = float(geo.get("longitude") or listing["longitude"])
    persons = int((acc.get("occupancy") or {}).get("value") or listing.get("maxSleeps") or 0)
    town = (acc.get("address") or {}).get("addressLocality") or listing.get("city") or ""

    # Broodkruimel: "Spain > Costa Brava > Lloret de Mar > Canyelles > Maylea"
    area = ""
    m = re.search(r"Spain > ([^>]+) > ([^>]+) > (?:([^>]+) > )?" + re.escape(name) + r"\b", text)
    if m and m.group(3):
        area = m.group(3).strip()

    beds = {}
    for n, kind in re.findall(r"(\d+)x ([A-Za-z -]+?) beds?\b", section(text, "BOOK THIS VILLA › Bedrooms", ["Bathrooms", "Facilities"])):
        beds[kind.strip()] = beds.get(kind.strip(), 0) + int(n)

    m2 = None
    m = re.search(r"built surface of (\d+)\s*m", text)
    if m:
        m2 = int(m.group(1))

    # Let op: de navigatiebalk bevat ook "Facilities and amenities Surroundings Services"
    m = re.search(r"Facilities and amenities (?!Surroundings)(.*?)(?:Arrival and departure times|Surroundings Show map|$)", text)
    facilities = m.group(1)[:3000] if m else ""
    m = re.search(r"Services (?:Included|Mandatory|Optional) services(.*?)(?:More information|$)", text)
    services = m.group(1)[:3000] if m else ""
    # Kop + omschrijving. Tussen kop en omschrijving staan beschikbaarheid en gastreviews;
    # die slaan we over, anders telt "de jacuzzi werkte niet" in een review als voorziening.
    start = text.rfind("Share this villa Share")
    end = text.find("BOOK THIS VILLA › Bedrooms", start)
    block = text[start:end if end > start else start + 8000] if start >= 0 else ""
    head_part = block.split("Availability", 1)[0]
    body_part = block.rsplit("Description Description", 1)[1] if "Description Description" in block else ""
    description = f"{head_part} {body_part}"

    features = set()
    amen = {a.get("name", "").lower(): a.get("value") for a in acc.get("amenityFeature") or []}
    if amen.get("private swimming pool") or listing.get("poolType") == "private":
        features.add("privatePool")
    for label, key in (("air conditioning", "airco"), ("barbecue", "bbq"), ("parking", "parking"), ("sea view", "seaView")):
        if amen.get(label):
            features.add(key)
    extra = set(text_features(facilities + " " + description)) - {"privatePool", "seaView"}
    features |= extra
    # Losse "safe" in vrije tekst is ambigu; alleen uit de voorzieningenlijst
    if "safe" in features and not re.search(r"\bsafe\b", facilities.lower()):
        features.discard("safe")

    airco = ""
    head_note = description.split("Availability", 1)[0]
    ac_note = re.search(r"([^.!>]*air ?condition[^.!]*[.!])", head_note, re.I)
    ac_cost = re.search(r"Air conditioning (€[\d.,]+[^A-Z]*)", services)
    if "airco" in features:
        parts = []
        if ac_note:
            parts.append(ac_note.group(1).strip())
        if ac_cost:
            parts.append("toeslag " + ac_cost.group(1).strip().rstrip(","))
        else:
            features.add("aircoFree")
        airco = " ".join(parts) or "ja"

    dist = {}
    sur = section(text, "Surroundings Nearest", ["Services", "More information"])
    for label, key in (("beach", "beach"), ("shop", "supermarket"), ("nightlife", "nightlife"), ("restaurants", "restaurant")):
        mm = re.search(r"%s: ([\d.,]+\s*k?m)\b" % label, "Nearest " + sur)
        if mm and meters(mm.group(1)) is not None:
            dist[key] = meters(mm.group(1))
    airports = [meters(x) for x in re.findall(r"(?:Girona|Barcelona|Reus)[^:]*: ([\d.,]+\s*k?m)\b", sur)]
    airports = [a for a in airports if a is not None]
    if airports:
        dist["airport"] = min(airports)
    if "beach" not in dist and listing.get("distanceBeach"):
        dist["beach"] = int(listing["distanceBeach"])

    groups = "onbekend"
    if re.search(r"groups of (young people|youngsters)[^.!]{0,60}? are allowed", text, re.I):
        groups = "groepen ok"
    elif re.search(r"groups of (young people|youngsters)[^.!]{0,60}? are not allowed|youngsters are not welcome", text, re.I):
        groups = "geen groepen"
    min_age = None
    m = re.search(r"average age (?:of )?(equal to or less than|less than or equal to|below or equal to|"
                  r"equal to or below|less than|below|under) (\d+)", text, re.I)
    if m:
        min_age = int(m.group(2)) + (1 if "equal" in m.group(1).lower() else 0)

    rating, reviews = None, 0
    ar = vr.get("aggregateRating") or acc.get("aggregateRating")
    if ar and ar.get("ratingValue"):
        best = float(ar.get("bestRating") or 10)
        rating = round(float(ar["ratingValue"]) / best * 5, 2)
        reviews = int(ar.get("ratingCount") or 0)

    images = []
    gal = component(page, "AccommodationImageGallery") or {}
    for img in gal.get("images") or listing.get("pictures") or []:
        p = img.get("thumbnailLarge") or img.get("webPath")
        if p:
            images.append(BASE + p if p.startswith("/") else p)
    if not images:
        images = [i for i in acc.get("image") or [] if isinstance(i, str)]
    images = images[:6]

    price = ""
    p = (acc.get("offers") or {}).get("price") or listing.get("minPrice")
    if p:
        price = f"vanaf €{round(float(p))} / nacht"

    head = section(text, "Share this villa Share", ["Availability"])
    return {
        "id": f"clubvillamar-{slug}",
        "source": "Club Villamar",
        "name": name,
        "url": url,
        "town": town,
        "area": area,
        "lat": lat, "lng": lng,
        "persons": persons,
        "personsMax": persons,
        "bedrooms": acc.get("numberOfBedrooms") or listing.get("numBedrooms"),
        "bathrooms": float(acc["numberOfBathroomsTotal"]) if acc.get("numberOfBathroomsTotal") is not None else listing.get("numBathrooms"),
        "beds": beds,
        "m2": m2,
        "features": [k for k in FEATURE_KEYS if k in features],
        "airco": airco,
        "seats": "",
        "dist": dist,
        "scores": {},
        "groups": groups,
        "minAge": min_age,
        "rating": rating, "reviews": reviews,
        "images": images,
        "luxury": bool(re.search(r"\b(luxury|premium)\b", head, re.I)),
        "price": price,
    }


def load(refresh=False):
    stats.update(fetched=0, cached=0)
    cands = {}
    for region, city in town_slugs(refresh):
        try:
            cands.update(candidates(region, city, refresh))
        except Exception as e:  # noqa: BLE001
            print(f"  waarschuwing clubvillamar: zoekpagina {city} mislukt: {e}")
    houses = []
    for i, (url, listing) in enumerate(sorted(cands.items())):
        if i >= MAX_DETAIL_PAGES:
            print(f"  waarschuwing clubvillamar: limiet van {MAX_DETAIL_PAGES} detailpagina's bereikt")
            break
        try:
            house = parse(url, fetch(url, refresh), listing)
        except Exception as e:  # noqa: BLE001
            print(f"  waarschuwing clubvillamar: {url} overgeslagen: {e}")
            continue
        if keep(house):
            houses.append(house)
    return houses


if __name__ == "__main__":
    hs = load()
    print(len(hs), "huizen")
    for h in hs:
        print(f"{h['name'][:22]:22} | {h['town'][:18]:18} | {h['persons']:>2}p {h['bedrooms']}sk | "
              f"{h['lat']:.4f},{h['lng']:.4f} | {','.join(h['features'])} | {h['groups']} | {h['price']}")
    print(f"pagina's: {stats['fetched']} gedownload, {stats['cached']} uit cache")
