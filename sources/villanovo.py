"""Villanovo (villanovo.com): luxe villa-verhuur aan de Costa Brava.

Werkwijze: sitemap-en.xml -> villa-URL's onder /villa-rentals/europe/spain/costa-brava/<dorp>/<slug>
-> detailpagina's (ld+json Hotel, data-latitude/-longitude, 'villa-page-details', voorzieningenlijsten).
/xhr/ is verboden in robots.txt en wordt niet gebruikt (daardoor maar 1 foto per villa).
"""
import json
import re

from sources.common import cached, clean, keep, km_from_lloret, meters, text_features

SOURCE = "villanovo"
BASE = "https://www.villanovo.com"
SITEMAP = BASE + "/sitemap-en.xml"
PREFIX = BASE + "/villa-rentals/europe/spain/costa-brava/"
MAX_PAGES = 120

# Dorpen die zeker > 30 km van Lloret liggen: niet eens ophalen
FAR_TOWNS = {
    "cadaques", "roses", "rosas", "llanca", "begur", "pals", "palafrugell", "calella-de-palafrugell",
    "llafranc", "tamariu", "platja-daro", "lestartit", "l-estartit", "estartit", "empuriabrava",
    "port-de-la-selva", "torroella-de-montgri", "figueres", "lescala", "l-escala",
}

stats = {"pages": 0}


def _urls(xml, depth=0):
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    if "<sitemapindex" in xml and depth < 2:
        out = []
        for sub in locs:
            try:
                out += _urls(_get(sub), depth + 1)
            except Exception as e:
                print(f"  waarschuwing villanovo: sub-sitemap {sub} mislukt: {e}")
        return out
    return locs


def _get(url, refresh=False):
    if "/xhr/" in url:
        raise ValueError("robots.txt verbiedt /xhr/")
    stats["pages"] += 1
    return cached(SOURCE, url, refresh)


def villa_urls(refresh=False):
    seen, out = set(), []
    for u in _urls(_get(SITEMAP, refresh)):
        if not u.startswith(PREFIX):
            continue
        parts = u[len(PREFIX):].split("/")
        # alleen <dorp>/<slug> zonder slash aan het eind en zonder filter (service:, for:, room:)
        if len(parts) != 2 or not parts[1] or ":" in u[len(PREFIX):]:
            continue
        if parts[0] in FAR_TOWNS or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[:MAX_PAGES]


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _lists(page):
    """Voorzieningen: <p class="ph">Kop</p><ul class="villa-nav-content-ul"><li>..</li></ul>."""
    out = {}
    for head, body in re.findall(r'<p class="ph">([^<]+)</p>\s*<ul class="villa-nav-content-ul">(.*?)</ul>', page, re.S):
        out[clean(head)] = [clean(li) for li in re.findall(r"<li>(.*?)</li>", body, re.S)]
    return out


def _conditions(page):
    m = re.search(r'<p class="ph">Rental conditions</p>(.*?)(?:<p class="ph">|</div>)', page, re.S)
    if not m:
        return []
    return [clean(x).lstrip("- ").strip() for x in re.split(r"<br\s*/?>", m.group(1)) if clean(x)]


def _groups(conds_text):
    t = conds_text.lower()
    if re.search(r"(no|not? (allowed|accepted|permitted)[^.]*)\b(groups?|bachelor|stag|hen)\b"
                 r"|\b(groups? of (young|youth)|young (people|groups?)|bachelor|stag|hen)[^.]*not (allowed|accepted)", t):
        return "geen groepen"
    if re.search(r"groups? (are )?(welcome|allowed|accepted)", t):
        return "groepen ok"
    return "onbekend"


def parse(url, page):
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    town_slug = url[len(PREFIX):].split("/")[0]

    hotel = {}
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Hotel":
            hotel = data
            break

    lat = re.search(r'data-latitude="(-?[\d.]+)"', page)
    lng = re.search(r'data-longitude="(-?[\d.]+)"', page)
    if not (lat and lng):
        raise ValueError("geen coördinaten")
    lat, lng = float(lat.group(1)), float(lng.group(1))

    # "10 guests (12 max.) • 5 bedrooms • 5 bathrooms • 400 m²"
    details = clean((re.search(r'class="villa-page-details">(.*?)</div>', page, re.S) or [None, ""])[1])
    g = re.search(r"(\d+)\s*guests?(?:\s*\((\d+)\s*max)?", details)
    persons = int(g.group(1)) if g else _int(re.search(r'data-capacity="(\d+)"', page) and
                                              re.search(r'data-capacity="(\d+)"', page).group(1))
    persons_max = int(g.group(2)) if g and g.group(2) else persons
    bedrooms = re.search(r"(\d+)\s*bedrooms?", details)
    bathrooms = re.search(r"(\d+(?:\.\d+)?)\s*bathrooms?", details)
    m2 = re.search(r"([\d ]+)\s*m²", details)

    address = hotel.get("address") or {}
    town = clean(address.get("addressLocality")) or town_slug.replace("-", " ").title()
    name = clean(hotel.get("name")) or clean((re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S) or [None, slug])[1])

    lists = _lists(page)
    included = {"Included in rates", "Not included in rates", "Compulsory extra costs"}
    amenities = [a for head, items in lists.items() if head not in included for a in items]
    am_text = " | ".join(amenities)
    desc = clean((re.search(r'<div class="villa-desc"[^>]*>(.*?)</div>', page, re.S) or [None, ""])[1])
    desc = desc or clean(hotel.get("description"))
    conds = _conditions(page)

    feats = set(text_features(desc + " " + am_text))
    al = am_text.lower()
    has_pool = re.search(r"swimming pool|\bpool\b(?! table)", al)
    shared = re.search(r"shared (swimming )?pool|communal pool|common pool", (al + " " + desc.lower()))
    if has_pool and not shared:
        feats.add("privatePool")  # hele villa wordt verhuurd; zwembad hoort bij de villa
    elif shared:
        feats.discard("privatePool")
    if re.search(r"jacuzzi|hot ?tub|spa\b", al):
        feats.add("jacuzzi")
    if "air conditioning" in al or "air-conditioning" in al:
        feats.add("airco")
    if re.search(r"sea view|view of the sea|ocean view", al):
        feats.add("seaView")
    airco = next((a for a in amenities if re.search(r"air ?-?condition", a, re.I)), "")

    dist = {}
    for a in amenities:
        d = meters(a)
        if d is None:
            continue
        al1 = a.lower()
        if "beach" in al1:
            dist.setdefault("beach", d)
        elif re.search(r"shop|supermarket|grocery", al1):
            dist.setdefault("supermarket", d)
        elif "restaurant" in al1:
            dist.setdefault("restaurant", d)
        elif "airport" in al1:
            dist.setdefault("airport", d)
    if "beach" not in dist and re.search(r"direct (sea|beach) access|direct access to the (beach|sea)", al):
        dist["beach"] = 0

    min_age = None
    ma = re.search(r"(?:minimum age|must be at least|aged? (?:over|at least))\D{0,20}(\d{2})", " ".join(conds), re.I)
    if ma:
        min_age = int(ma.group(1))

    rating, reviews = None, 0
    agg = hotel.get("aggregateRating") or {}
    try:
        best = float(agg.get("bestRating") or 5)
        rating = round(float(agg["ratingValue"]) / best * 5, 2)
        reviews = int(agg.get("ratingCount") or 0)
    except (KeyError, TypeError, ValueError):
        pass

    # Afbeeldingen: alleen die van deze villa (id-prefix), niet de "vergelijkbare villa's"
    images = []
    vid = re.search(r"/photos/(\d+)/", str(hotel.get("image") or ""))
    og = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    if not vid and og:
        vid = re.search(r"/(\d+)-", og.group(1))
    vid = vid.group(1) if vid else None
    cands = [og.group(1)] if og else []
    if hotel.get("image"):
        cands.append(str(hotel["image"]))
    if vid:
        cands += re.findall(r"https://imagedelivery\.net/[^/\"'\s]+/%s-[^/\"'\s]+/w=1920[^\"'\s]*" % vid, page)
    bases = set()
    for c in cands:
        base = re.sub(r"/w=[^/]*$", "", c)
        if base in bases:
            continue
        bases.add(base)
        images.append(c)
    images = images[:6]

    price = ""
    fp = re.search(r'class="from-price"[^>]*>\s*([\d  .,]+)\s*EUR', page)
    if fp:
        amount = re.sub(r"[^\d]", "", fp.group(1))
        if amount:
            price = f"vanaf €{int(amount)} / nacht"
    elif hotel.get("priceRange"):
        pr = re.search(r"€\s*([\d.,]+)", hotel["priceRange"])
        if pr:
            price = f"vanaf €{pr.group(1)} / nacht"

    return {
        "id": f"villanovo-{slug}",
        "source": "Villanovo",
        "name": name,
        "url": url,
        "town": town,
        "area": "",
        "lat": lat, "lng": lng,
        "persons": persons or 0,
        "personsMax": max(persons_max or 0, persons or 0),
        "bedrooms": int(bedrooms.group(1)) if bedrooms else _int(hotel.get("numberOfRooms")),
        "bathrooms": float(bathrooms.group(1)) if bathrooms else None,
        "beds": {},
        "m2": int(m2.group(1).replace(" ", "")) if m2 and m2.group(1).strip() else None,
        "features": sorted(feats),
        "airco": airco,
        "seats": "",
        "dist": dist,
        "scores": {},
        "groups": _groups(" ".join(conds)),
        "minAge": min_age,
        "rating": rating, "reviews": reviews,
        "images": images,
        "luxury": True,
        "price": price,
    }


def load(refresh=False):
    stats["pages"] = 0
    try:
        urls = villa_urls(refresh)
    except Exception as e:
        print(f"  waarschuwing villanovo: sitemap mislukt: {e}")
        return []
    houses = []
    for url in urls:
        try:
            house = parse(url, _get(url, refresh))
        except Exception as e:
            print(f"  waarschuwing villanovo: {url} overgeslagen: {e}")
            continue
        if keep(house):
            houses.append(house)
    return houses


if __name__ == "__main__":
    result = load()
    print(f"Villanovo: {len(result)} huizen")
    for h in result:
        print(f"- {h['name']} | {h['town']} | {km_from_lloret(h['lat'], h['lng']):.1f} km | "
              f"{h['persons']}/{h['personsMax']} pers | {h['bedrooms']} slk | {h['lat']:.5f},{h['lng']:.5f} | "
              f"{','.join(h['features'])} | {h['price']} | groups={h['groups']} rating={h['rating']} "
              f"({h['reviews']}) m2={h['m2']} dist={h['dist']} airco={h['airco']!r} imgs={len(h['images'])}")
    print(f"pagina's opgehaald (incl. cache): {stats['pages']}")
