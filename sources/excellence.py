"""Excellence Luxury Villas (excellenceluxuryvillas.com) - UK luxury agency.

Werkwijze: sitemap_index.xml -> estate_property-sitemapN.xml -> property-URL's
waarvan het pad een plaats rond Lloret noemt (plus de generieke /costa-brava/-URL's,
die geen plaats in de slug hebben). Detailpagina's hebben schema.org VacationRental
ld+json (geo, occupancy, slaapkamers, badkamers, prijs) en een HTML-featurelijst.
"""
import json
import re

from sources.common import cached, clean, meters, text_features, keep

SOURCE = "excellence"
BASE = "https://excellenceluxuryvillas.com"
MAX_PAGES = 150

# Plaatsen binnen ~30 km van Lloret die in de URL-paden voorkomen
TOWN_RE = re.compile(
    r"lloret|tossa-|tossa/|blanes|fenals|santa-cristina|sta-cristina|cristina-daro|canyelles-lloret|"
    r"sant-feliu|vidreres|calella-de-mar|malgrat|pineda-de-mar|santa-susanna|malavella|llagostera|"
    r"macanet|sagaro|platja-d-aro|playa-de-aro|-aro/|calonge|palamos|romanya"
)
EXCLUDE_RE = re.compile(r"/balearic|/turkiye|/italy|/sicily|/caceres|/la-cumbre")

# Gestructureerde featurenamen (HTML-lijst) -> FEATURE_KEYS
FEATURE_MAP = [
    (r"^private pool", "privatePool"),
    (r"air[- ]?con", "airco"),
    (r"bbq|barbecue", "bbq"),
    (r"parking|garage", "parking"),
    (r"jacuzzi|hot ?tub|whirlpool", "jacuzzi"),
    (r"sauna", "sauna"),
    (r"lift|elevator", "lift"),
    (r"sea ?view", "seaView"),
    (r"dishwasher", "dishwasher"),
    (r"\bsafe\b", "safe"),
    (r"table tennis|ping ?pong|pool table|billiard|foosball|games room", "games"),
    (r"sound system|sonos", "sound"),
]

GENERIC_PLACES = {"spain", "europe", "catalonia", "costa brava", "girona", "girona region", "catalunya", "barcelona"}


def _property_urls(refresh):
    index = cached(SOURCE, BASE + "/sitemap_index.xml", refresh)
    maps = [u for u in re.findall(r"<loc>(.*?)</loc>", index) if "estate_property-sitemap" in u]
    urls = []
    for m in maps:
        try:
            urls += re.findall(r"<loc>(.*?)</loc>", cached(SOURCE, m, refresh))
        except Exception as e:  # noqa: BLE001
            print(f"  waarschuwing: sitemap {m} mislukt: {e}")
    urls = [u for u in dict.fromkeys(urls) if "/wp-content/" not in u]

    def slug(u):
        return u.rstrip("/").split("/")[-1]

    town, generic, seen = [], [], set()
    for u in urls:
        path = u.split(".com", 1)[-1]
        if EXCLUDE_RE.search(path):
            continue
        if TOWN_RE.search(path):
            town.append(u)
    for u in urls:
        if "/costa-brava/" in u and u not in town:
            generic.append(u)
    out = []
    for u in town + generic:  # plaats-URL's eerst, dan generieke Costa Brava
        if slug(u) not in seen:
            seen.add(slug(u))
            out.append(u)
    return out[:MAX_PAGES]


def _ld_rental(page):
    for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for node in data.get("@graph", [data]) if isinstance(data, dict) else data:
            if isinstance(node, dict) and node.get("@type") == "VacationRental":
                return node
    return None


def _num(v, cast=int):
    try:
        return cast(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _fmt_gbp(v):
    n = _num(v)
    return f"£{n:,}".replace(",", ".") if n else ""


def _details(page):
    """Paren 'label: waarde' uit de listing_detail-panelen."""
    out = {}
    for m in re.finditer(r'<div class="listing_detail list_detail_prop_(\w+)[^"]*"><span class="item_head">[^<]*</span>(.*?)</div>', page, re.S):
        out[m.group(1)] = clean(m.group(2))
    return out


def _features_html(page):
    i = page.find('id="listing_ammenities"')
    if i < 0:
        return []
    block = page[i:page.find('id="listing_calendar"', i) if page.find('id="listing_calendar"', i) > 0 else i + 20000]
    return [clean(x) for x in re.findall(r'<i class="fas fa-check checkon"></i>([^<]+)</div>', block)]


def _description(page):
    m = re.search(r'id="listing_description_content"[^>]*>(.*?)</div>\s*</div>', page, re.S)
    return clean(m.group(1)) if m else ""


def _images(page):
    i = page.find("image_gallery lightbox_trigger")
    part = page[i:i + 60000] if i >= 0 else page
    imgs = []
    for u in re.findall(r'https://[^"\'\s)]+/wp-content/uploads/\d{4}/\d{2}/[^"\'\s)?]+\.(?:jpe?g|webp|png)', part):
        if "logo" in u.lower() or u in imgs:
            continue
        imgs.append(u)
    return imgs[:6]


def _km_text(t):
    return re.sub(r"(\d[\d.,]*)\s*(kilometres|kilometers|km)\b", r"\1 km", re.sub(r"(\d[\d.,]*)\s*(metres|meters)\b", r"\1 m", t))


def _dist(desc):
    t = _km_text(desc)
    dist = {}
    pats = {
        "beach": r"beach[^.]{0,60}?(\d[\d.,]*\s*k?m)\b|(\d[\d.,]*\s*k?m)\b[^.]{0,25}(?:from|to) (?:the )?(?:nearest |main )?(?:beach|mediterranean|sea|coast)",
        "airport": r"airport[^.]{0,30}?(\d[\d.,]*\s*k?m)\b",
        "supermarket": r"(?:supermarket|shops)[^.]{0,40}?(\d[\d.,]*\s*k?m)\b",
        "restaurant": r"restaurants?[^.]{0,40}?(\d[\d.,]*\s*k?m)\b",
        "nightlife": r"(?:town cent(?:re|er)|nightlife)[^.]{0,40}?(\d[\d.,]*\s*k?m)\b",
    }
    for key, p in pats.items():
        # kleinste waarde (bijv. Girona Airport i.p.v. Barcelona Airport)
        vals = [meters(next(g for g in m.groups() if g)) for m in re.finditer(p, t, re.I)]
        vals = [v for v in vals if v is not None and v < 200000]
        if vals:
            dist[key] = min(vals)
    return dist


def _groups(text):
    t = text.lower()
    if re.search(r"no (stag|hen)|(stag|hen)(?: and hen| or hen)? (parties|groups|dos)[^.]{0,20}not (allowed|permitted|accepted)|"
                 r"not suitable for (stag|hen|young)|no groups|groups (are )?not (allowed|permitted|accepted)|"
                 r"no (parties|events)[^.]{0,40}groups|young groups[^.]{0,20}not", t):
        return "geen groepen"
    if re.search(r"(stag|hen) (parties|groups|dos)[^.]{0,30}(welcome|accepted|allowed)|groups of friends (are )?welcome|"
                 r"mixed groups", t):
        return "groepen ok"
    return "onbekend"


def _parse(url, page):
    ld = _ld_rental(page)
    if not ld:
        raise ValueError("geen VacationRental ld+json")
    geo = ld.get("geo") or {}
    lat, lng = _num(geo.get("latitude"), float), _num(geo.get("longitude"), float)
    if lat is None or lng is None:
        raise ValueError("geen coordinaten")
    place = ld.get("containsPlace") or {}
    amen = {a.get("name"): a.get("value") for a in place.get("amenityFeature", []) if isinstance(a, dict)}
    persons = _num((ld.get("occupancy") or {}).get("value")) or _num(amen.get("Maximum Guests"))
    if not persons:
        m = re.search(r"(\d+)\s*Guests</div>", page)
        persons = int(m.group(1)) if m else None

    details = _details(page)
    feats_html = _features_html(page)
    desc = _description(page)

    # Plaats: ld-adres, anders meest specifieke plaats uit het City-detail
    # (oudere pagina's zetten 'Lloret de Mar' bij bijna alles in de lijst; kies liefst een andere specifieke plaats)
    places = [c.strip() for c in clean((ld.get("address") or {}).get("addressLocality", "")).split(",") if c.strip()]
    places += [c.strip() for c in details.get("city", "").split(",") if c.strip()]
    specific = list(dict.fromkeys(c for c in places if c.lower() not in GENERIC_PLACES))
    if len(specific) > 1 and "Lloret de Mar" in specific:
        specific.remove("Lloret de Mar")
    town = specific[0] if specific else (places[0] if places else "")

    area = ""
    m = re.search(r"\bin the (?:exclusive |residential |quiet |peaceful |prestigious |sought-after )*([A-Z][\w'À-ſ-]+(?: [A-Z][\w'À-ſ-]+)*) (?:area|urbani[sz]ation|neighbourhood|residential area)", desc)
    if m and m.group(1).lower() not in GENERIC_PLACES and m.group(1) != town:
        area = m.group(1)

    feats = set()
    for f in feats_html:
        for p, key in FEATURE_MAP:
            if re.search(p, f, re.I):
                feats.add(key)
    feats.update(text_features(desc + " " + " ".join(feats_html)))
    if "privatePool" in feats and re.search(r"shared (swimming )?pool|communal (swimming )?pool", desc, re.I) \
            and not re.search(r"^private pool", " ".join(feats_html), re.I):
        feats.discard("privatePool")

    airco = ""
    m = re.search(r"[^.]*\bair[- ]?condition\w*[^.]*\.", desc, re.I)
    if m:
        airco = m.group(0).strip()[:160]
    elif any(re.search(r"air ?con", f, re.I) for f in feats_html):
        airco = "Air conditioning"

    m2 = None
    for m in re.finditer(r"(\d[\d,.]*)\s*(?:square met(?:re|er)s|sq\.? ?m\b|m2\b|m²)", desc, re.I):
        if re.search(r"plot|garden|land|grounds|estate|terrace|pool", desc[max(0, m.start() - 40):m.end() + 15], re.I):
            continue
        m2 = _num(m.group(1).replace(".", ""))
        if m2 and not 40 <= m2 <= 2000:
            m2 = None
        break

    min_age = None
    m = re.search(r"(?:minimum age|aged?|over)\s*(?:of\s*)?(\d{2})\s*(?:\+|or over|years)", desc, re.I)
    if m and re.search(r"book|guest|tenant|lead", desc[max(0, m.start() - 80):m.end() + 80], re.I):
        min_age = int(m.group(1))

    offers = ld.get("offers") or {}
    week = _fmt_gbp((offers.get("priceSpecification") or {}).get("price"))
    night = _fmt_gbp(offers.get("price"))
    if week:
        price = f"vanaf {week} / week"
    elif night:
        price = f"vanaf {night} / nacht"
    else:
        m = re.search(r"Price per night:</span>\s*From\s*£([\d,]+)", page)
        price = f"vanaf {_fmt_gbp(m.group(1))} / nacht" if m else ""

    name = clean(ld.get("name") or "")
    name = name.split(":")[0].strip() or name
    slug = url.rstrip("/").split("/")[-1]

    return {
        "id": f"excellence-{slug}",
        "source": "Excellence Luxury Villas",
        "name": name,
        "url": url,
        "town": town,
        "area": area,
        "lat": lat,
        "lng": lng,
        "persons": persons,
        "personsMax": persons,
        "bedrooms": _num(ld.get("numberOfBedrooms") or place.get("numberOfBedrooms")),
        "bathrooms": _num(ld.get("numberOfBathroomsTotal") or place.get("numberOfBathroomsTotal"), float),
        "beds": {},
        "m2": m2,
        "features": sorted(feats),
        "airco": airco,
        "seats": "",
        "dist": _dist(desc),
        "scores": {},
        "groups": _groups(desc),
        "minAge": min_age,
        # aggregateRating in de ld+json is een site-brede agency-score (identiek op elke pagina), dus niet gebruikt
        "rating": None,
        "reviews": 0,
        "images": _images(page),
        "luxury": True,
        "price": price,
    }


def load(refresh=False):
    urls = _property_urls(refresh)
    load.pages = len(urls)
    houses = []
    for url in urls:
        try:
            page = cached(SOURCE, url, refresh)
            house = _parse(url, page)
        except Exception as e:  # noqa: BLE001
            print(f"  waarschuwing: {url} overgeslagen: {e}")
            continue
        if keep(house):
            houses.append(house)
    return houses


if __name__ == "__main__":
    result = load()
    print(f"{len(result)} huizen (Excellence Luxury Villas)")
    for h in result:
        print(f"- {h['name']} | {h['town']}{' / ' + h['area'] if h['area'] else ''} | {h['persons']}p {h['bedrooms']}sk "
              f"| {h['lat']:.5f},{h['lng']:.5f} | {','.join(h['features'])} | {h['price']} | m2={h['m2']} dist={h['dist']} "
              f"groups={h['groups']} imgs={len(h['images'])}")
    print(f"{getattr(load, 'pages', 0)} detailpagina's opgehaald")
