"""AgriPrix : cœur de l'aide à la décision de vente (FRANÇAIS uniquement).

Règle d'or : aucun prix, aucun calcul ne vient d'un modèle de langage. Le modèle ne fait que comprendre la
demande ; tous les chiffres viennent de la base `data/prices.db` et des formules de ce fichier.

Ce module est SANS ÉTAT (compatible Vercel) : l'état du dialogue (informations déjà données, questions
posées) est renvoyé au navigateur dans `state`, qui le renvoie à la requête suivante.
"""
import difflib
import functools
import json
import math
import time
import os
import re
import sqlite3
import statistics
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from . import store

# ----------------------------------------------------------------------------- configuration
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("PRICES_DB", str(ROOT / "data" / "prices.db"))
MAX_DISTANCE_KM = float(os.getenv("MAX_DISTANCE_KM", "150"))
ROAD_FACTOR = float(os.getenv("ROAD_FACTOR", "1.3"))  # distance routière ≈ 1,3 × distance à vol d'oiseau
TRANSPORT_FCFA_PAR_KG_KM = float(os.getenv("TRANSPORT_FCFA_PAR_KG_KM", "0.15"))  # HYPOTHÈSE à valider avec le terrain
FRESH_DAYS = 90  # un prix plus ancien que cela (par rapport au plus récent du produit) est exclu / signalé
SEUIL_VERDICT = 0.10  # ±10 % autour du prix de référence
UNITES_OK = ("KG", "kg", "Kg")
SOURCE = "WFP via HDX HAPI"

# Synonymes français -> nom EXACT du registre
SYNONYMS = {
    "tomate": "Tomatoes", "tomates": "Tomatoes",
    "oignon": "Onions", "oignons": "Onions",
    "riz": "Rice (imported)", "riz importe": "Rice (imported)",
    "mais": "Maize (white)", "mais blanc": "Maize (white)",
    "sorgho": "Sorghum (red)",
    "gari": "Cassava meal (gari)",  # « manioc » volontairement absent (manioc frais != gari)
    "tapioca": "Cassava meal (tapioca)",
    "haricot": "Beans (white)", "haricots": "Beans (white)", "niebe": "Beans (white)",
    "soja": "Soybeans",
    "huile de palme": "Oil (palm)", "huile palme": "Oil (palm)",
    "huile d arachide": "Oil (groundnut)", "huile arachide": "Oil (groundnut)",
    "piment": "Peppers (red, dry)", "piment sec": "Peppers (red, dry)",
    "pois bambara": "Groundnuts (Bambara)", "voandzou": "Groundnuts (Bambara)",
    "poisson": "Fish (fresh, silvi)", "orange": "Oranges", "oranges": "Oranges",
}
LABEL_FR = {
    "Coconut (dried)": "noix de coco séchée", "Leafy vegetables": "légumes feuilles", "Peas (green, dry)": "petit pois secs",
    "Shrimps": "crevettes", "Snail": "escargot",
    "Tomatoes": "tomate", "Onions": "oignon", "Rice (imported)": "riz importé", "Maize (white)": "maïs blanc",
    "Sorghum (red)": "sorgho rouge", "Cassava meal (gari)": "gari", "Cassava meal (tapioca)": "tapioca",
    "Beans (white)": "haricot blanc", "Soybeans": "soja", "Oil (palm)": "huile de palme",
    "Oil (groundnut)": "huile d'arachide", "Peppers (red, dry)": "piment rouge sec",
    "Groundnuts (Bambara)": "pois bambara", "Fish (fresh, silvi)": "poisson frais", "Oranges": "orange",
}
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


# Traduction automatique des noms HDX/WFP (anglais) pour les produits absents de LABEL_FR : « Beans (black) » -> « haricot noir ».
BASE_FR = {
    "beans": "haricot", "cowpeas": "niébé", "cabbage": "chou", "carrots": "carotte", "cassava": "manioc",
    "cassava flour": "farine de manioc", "cassava meal": "semoule de manioc", "cassava chips": "cossettes de manioc",
    "maize": "maïs", "maize flour": "farine de maïs", "maize meal": "farine de maïs", "millet": "mil", "rice": "riz",
    "sorghum": "sorgho", "yams": "igname", "yam": "igname", "yam flour": "farine d'igname", "oil": "huile",
    "groundnuts": "arachide", "peanuts": "arachide", "tomatoes": "tomate", "onions": "oignon", "peppers": "piment",
    "fish": "poisson", "oranges": "orange", "soybeans": "soja", "bananas": "banane", "plantains": "banane plantain",
    "eggs": "œufs", "meat": "viande", "meat (beef)": "viande de bœuf", "beef": "bœuf", "chicken": "poulet",
    "goat meat": "viande de chèvre", "okra": "gombo", "garlic": "ail", "ginger": "gingembre", "lettuce": "laitue",
    "cucumbers": "concombre", "eggplants": "aubergine", "spinach": "épinard", "sugar": "sucre", "salt": "sel",
    "bread": "pain", "wheat": "blé", "wheat flour": "farine de blé", "sesame": "sésame", "cashew nuts": "noix de cajou",
    "potatoes": "pomme de terre", "sweet potatoes": "patate douce", "taro": "taro", "pineapples": "ananas",
    "mangoes": "mangue", "avocados": "avocat", "papaya": "papaye", "lemons": "citron", "milk": "lait", "tapioca": "tapioca",
    "bambara": "voandzou", "shea butter": "beurre de karité", "charcoal": "charbon", "palm nut": "noix de palme",
}
QUAL_FR = {
    "black": "noir", "red": "rouge", "white": "blanc", "yellow": "jaune", "green": "vert", "imported": "importé", "local": "local",
    "fine": "fin", "coarse": "grossier", "dry": "sec", "dried": "séché", "fresh": "frais", "niebe": "niébé", "shelled": "décortiquée",
    "unshelled": "en coque", "palm": "de palme", "vegetable": "végétale", "groundnut": "d'arachide", "cotton": "de coton",
    "smoked": "fumé", "frozen": "congelé", "small": "petit", "large": "gros", "ripe": "mûr", "unripe": "vert", "gari": "gari",
}


def label(produit):
    """Nom français d'un produit du registre (jamais d'invention : sans correspondance, le nom d'origine est conservé)."""
    if produit in LABEL_FR:
        return LABEL_FR[produit]
    m = re.match(r"^\s*([^()]+?)\s*(?:\((.*)\))?\s*$", str(produit))
    if not m:
        return produit
    base, qual = m.group(1).strip(), (m.group(2) or "")
    b = BASE_FR.get(base.lower())
    if b is None:
        return produit
    quals = [q.strip().lower() for q in qual.split(",") if q.strip()]
    if base.lower() == "cassava meal" and "gari" in quals:
        b, quals = "gari", [q for q in quals if q != "gari"]
    return " ".join([b] + [QUAL_FR.get(q, q) for q in quals])


def non_traduits():
    """Produits du registre dont le nom reste en anglais (à compléter dans BASE_FR / QUAL_FR)."""
    return sorted(p for p in load_lists()[0] if label(p) == p)


# ----------------------------------------------------------------------------- utilitaires
def norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


_conn = None


def db():
    """Connexion en lecture seule (le disque de Vercel est en lecture seule)."""
    global _conn
    if _conn is None:
        if not Path(DB_PATH).exists():
            raise FileNotFoundError("Base de prix absente : lancez scripts/build_data.py (voir README).")
        uri = f"file:{Path(DB_PATH).resolve().as_posix()}?mode=ro&immutable=1"
        _conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    return _conn


def reset_db():
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None
    _base_lists.cache_clear()


def fmt(n):
    return f"{round(n):,}".replace(",", " ")


def virg(x):
    return f"{x:g}".replace(".", ",")


def fmt_date(d):
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return str(d)


def date_parlee(d):
    try:
        x = datetime.strptime(str(d)[:10], "%Y-%m-%d")
        return f"{MOIS[x.month - 1]} {x.year}"
    except Exception:
        return str(d)


def haversine(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


@functools.lru_cache(maxsize=1)
def _base_lists():
    c = db()
    products = [r[0] for r in c.execute("SELECT DISTINCT product FROM prices WHERE product IS NOT NULL")]
    zones = [r[0] for r in c.execute("SELECT DISTINCT zone FROM prices WHERE zone IS NOT NULL")]
    return products, zones


_extras = {"t": 0.0, "produits": [], "zones": [], "coords": {}}


def recharger_extras():
    _extras["t"] = 0.0


def extras():
    """Produits et marchés ajoutés par l'administrateur (base des contributions), relus toutes les 60 s.
    Base absente ou en panne : on garde la dernière lecture, ou rien, et l'application reste sur la copie HDX."""
    if time.time() - _extras["t"] > 60:
        try:
            rows = store.lire_ajouts()
            _extras.update(produits=[r[1] for r in rows if r[0] == "produit"], zones=[r[1] for r in rows if r[0] == "zone"],
                           coords={r[1]: (r[2], r[3]) for r in rows if r[0] == "zone" and r[2] is not None and r[3] is not None})
        except store.StoreIndisponible:
            pass
        _extras["t"] = time.time()
    return _extras


def load_lists():
    p, z = _base_lists()
    e = extras()
    return p + [x for x in e["produits"] if x not in p], z + [x for x in e["zones"] if x not in z]


def best_match(text, candidates, cutoff):
    words = norm(text).split()
    index = {norm(c): c for c in candidates}
    best, score = None, 0.0
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            frag = " ".join(words[i:i + n])
            if len(frag) < 4:
                continue
            for key in difflib.get_close_matches(frag, list(index), n=1, cutoff=cutoff):
                s = difflib.SequenceMatcher(None, frag, key).ratio()
                if s > score:
                    best, score = index[key], s
    return best


def match_product(text):
    products, _ = load_lists()
    padded = " " + norm(text) + " "
    for phrase in sorted(SYNONYMS, key=len, reverse=True):
        if f" {norm(phrase)} " in padded and SYNONYMS[phrase] in products:
            return SYNONYMS[phrase]
    sing = lambda t: " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in norm(t).split())
    texte_s, trouve = " " + sing(text) + " ", None
    for p in sorted(products, key=lambda x: -len(label(x))):       # noms français complets : « haricot noir », « farine de manioc »
        f = sing(label(p))
        if f and f != sing(p) and f" {f} " in texte_s:
            trouve = p
            break
    if trouve:
        return trouve
    key = best_match(text, [k for k in SYNONYMS if SYNONYMS[k] in products], 0.8)  # fautes de transcription
    return SYNONYMS[key] if key else best_match(text, products, 0.8)


CUES = {"a", "au", "aux", "de", "d", "du", "des", "sur", "vers", "dans", "marche", "en", "chez"}


def match_zone(text):
    """Nom exact d'abord ; correspondance approximative seulement après un mot de lieu (à, au, marché de...)
    ou si la réponse est très courte : évite de prendre « comme » pour Comè ou « veux » pour une commune."""
    words = norm(text).split()
    index = {norm(z): z for z in load_lists()[1]}
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            frag = " ".join(words[i:i + n])
            if frag in index:
                return index[frag]
    court = len(words) <= 3
    best, score = None, 0.0
    for i, w in enumerate(words):
        if not (court or (i > 0 and words[i - 1] in CUES)):
            continue
        for n in (3, 2, 1):
            frag = " ".join(words[i:i + n])
            if len(frag) < 4:
                continue
            for key in difflib.get_close_matches(frag, list(index), n=1, cutoff=0.78):
                sc = difflib.SequenceMatcher(None, frag, key).ratio()
                if sc > score:
                    best, score = index[key], sc
    return best


def parse_nombre(s):
    s = re.sub(r"\s", "", str(s)).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ----------------------------------------------------------------------------- extraction (règles locales ; le LLM est dans services.py)
CLES = ("produit", "quantite_kg", "lieu", "prix_propose_kilo", "cout_revient_kilo")


def extraire_regles(texte):
    t = " " + texte.lower().replace("\xa0", " ").replace(" ", " ") + " "
    d = {k: None for k in CLES}
    m = re.search(r"(?:co[uû]t(?:e|ent)?|revient|produire|charges?)[^\d]{0,40}(\d[\d\s]*(?:[.,]\d+)?)\s*(?:f\b|fcfa|francs?)", t)
    if m:
        d["cout_revient_kilo"] = parse_nombre(m.group(1))
        t = t[:m.start()] + " " + t[m.end():]
    m = re.search(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(?:f\b|fcfa|francs?)(?:\s*cfa)?\s*(?:le|par|/|\s)\s*(?:kilo|kg|k\b)", t)
    if m:
        d["prix_propose_kilo"] = parse_nombre(m.group(1))
    m = re.search(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(tonnes?|t\b|kilos?|kg)\b", t)
    if m:
        q = parse_nombre(m.group(1))
        if q is not None:
            d["quantite_kg"] = q * 1000 if m.group(2).startswith("t") else q
    d["produit"] = match_product(texte)
    d["lieu"] = match_zone(texte)
    return d


def _nombre_positif(v):
    if isinstance(v, str):
        v = parse_nombre(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 and math.isfinite(v):
        return float(v)
    return None


def fusionner(llm, regles):
    """Le LLM prime, les règles complètent ; seuls des nombres positifs sont acceptés."""
    llm = llm or {}
    d = {}
    for k in CLES:
        v = llm.get(k) if llm.get(k) not in (None, "", 0) else regles.get(k)
        d[k] = _nombre_positif(v) if k in ("quantite_kg", "prix_propose_kilo", "cout_revient_kilo") else (str(v) if v else None)
    return d


# ----------------------------------------------------------------------------- validation et dialogue
QUESTIONS = {
    "produit": "Quel produit voulez-vous vendre ?",
    "lieu": "Dans quel marché ou quelle commune se trouve l'offre ?",
    "quantite_kg": "Quelle quantité avez-vous, en kilos ?",
}


def valider(d):
    """Rapproche produit et lieu des listes du registre ; renvoie (donnees_validees, champs_manquants, notes)."""
    v, notes, manquants = dict(d), [], []
    if d.get("produit"):
        p = match_product(str(d["produit"]))
        if p:
            v["produit"] = p
        else:
            notes.append(f"Je n'ai pas de prix pour {d['produit']} dans ma base.")
            v["produit"] = None
    if d.get("lieu"):
        z = match_zone(str(d["lieu"]))
        if z:
            v["lieu"] = z
        else:
            notes.append(f"Je ne trouve pas {d['lieu']} parmi les marchés connus.")
            v["lieu"] = None
    for k in ("produit", "lieu", "quantite_kg"):
        if not v.get(k):
            manquants.append(k)
    return v, manquants, notes


def reponse_champ(champ, texte):
    """Réponse courte à une question posée (« Allada », « 1000 kg »...)."""
    if champ == "produit":
        return match_product(texte)
    if champ == "lieu":
        return match_zone(texte)
    if champ == "quantite_kg":
        m = re.search(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(tonnes?|t\b|kilos?|kg)?", texte.lower())
        if m:
            q = parse_nombre(m.group(1))
            if q is not None and m.group(2) and m.group(2).startswith("t"):
                q *= 1000
            return q
    return None


# ----------------------------------------------------------------------------- registre et calcul (déterministe)
def _lignes(produit, zone):
    q = ("SELECT price, date, market, price_type, price_flag FROM prices WHERE product=? AND zone=? "
         "AND unit IN (%s) ORDER BY date DESC" % ",".join("?" * len(UNITES_OK)))
    return db().execute(q, (produit, zone, *UNITES_OK)).fetchall()


def _rang(r):
    return ((r[4] or "") != "actual", (r[3] or "").lower() != "retail")


def dernier_prix(produit, zone, depuis=None):
    rows = _lignes(produit, zone)
    if depuis:
        rows = [r for r in rows if r[1] >= depuis]
    if not rows:
        return None
    last = rows[0][1]
    same = [r for r in rows if r[1] == last]
    best = min(_rang(r) for r in same)
    pick = sorted((r for r in same if _rang(r) == best), key=lambda r: r[0])
    m = pick[len(pick) // 2]
    return {"prix": m[0], "date": m[1], "marche": m[2], "type": m[3], "flag": m[4]}


def historique(produit, zone, jusqu_a, jours=365):
    rows = _lignes(produit, zone)
    if not rows:
        return None
    debut = (datetime.strptime(jusqu_a[:10], "%Y-%m-%d") - timedelta(days=jours)).strftime("%Y-%m-%d")
    rows = [r for r in rows if r[1] >= debut and (r[3] or "").lower() == "retail"] or [r for r in rows if r[1] >= debut]
    if len(rows) < 3:
        return None
    p = [r[0] for r in rows]
    return {"mediane": statistics.median(p), "min": min(p), "max": max(p), "n": len(p)}


def coords(zone):
    if zone in extras()["coords"]:
        return extras()["coords"][zone]
    r = db().execute("SELECT AVG(lat), AVG(lon) FROM prices WHERE zone=? AND lat IS NOT NULL AND lon IS NOT NULL", (zone,)).fetchone()
    return (r[0], r[1]) if r and r[0] is not None else None


# ----------------------------------------------------------------------------- contributions croisées avec HDX
JOURS_COLLECTE = 14   # une contribution compte pendant 14 jours
MIN_SANS_HDX = 5     # produit ou marché sans relevé HDX : 5 appareils différents au minimum
MIN_COLLECTE = 3      # il faut au moins 3 contributions cohérentes pour que le prix collecté fasse référence
TOLERANCE_ACCORD = 0.25  # collecte et HDX « concordent » à ±25 %


def crowd_all(jours=JOURS_COLLECTE):
    """{produit: {zone: {prix (médiane), n, type, date}}}. Vide si la base des contributions est absente ou en panne :
    l'application répond alors avec la seule copie HDX."""
    try:
        rows = store.recentes(jours)
    except store.StoreIndisponible:
        return {}
    groupes = {}
    for produit, zone, prix, type_prix, cree, appareil in rows:
        groupes.setdefault((produit, zone), {}).setdefault((type_prix or "Retail"), []).append((prix, cree, appareil))
    out = {}
    for (produit, zone), par_type in groupes.items():
        # on ne mélange pas détail et gros : on garde le type le mieux fourni (détail en cas d'égalité)
        t = max(par_type, key=lambda k: (len(par_type[k]), k == "Retail"))
        vals = par_type[t]
        out.setdefault(produit, {})[zone] = {"prix": statistics.median(v[0] for v in vals), "n": len(vals), "type": t,
                                             "date": max(v[1] for v in vals)[:10],
                                             "appareils": len({v[2] for v in vals})}
    return out


def resoudre(produit, zone, crowd, depuis=None):
    """Prix de référence d'un produit dans une zone, en croisant la copie HDX et les contributions récentes.
    Renvoie None, ou un dict : prix, date, marche, type, source ('collecte' | 'hdx'), fiabilite, n_contrib, prix_hdx..."""
    h = dernier_prix(produit, zone, depuis)
    c = (crowd or {}).get(zone)
    if not h and not c:
        return None
    base = {"prix_hdx": h["prix"] if h else None, "date_hdx": h["date"] if h else None,
            "prix_collecte": c["prix"] if c else None, "n_contrib": c["n"] if c else 0}
    if c and (c["n"] >= MIN_COLLECTE or not h):
        if c["n"] < MIN_COLLECTE or (not h and c.get("appareils", c["n"]) < MIN_SANS_HDX):
            fiab = "Peu de données"
        elif h and abs(c["prix"] - h["prix"]) / h["prix"] <= TOLERANCE_ACCORD:
            fiab = "Fiable"
        else:
            fiab = "Indicatif"
        return {**base, "prix": c["prix"], "date": c["date"], "marche": zone, "type": c["type"], "flag": "actual",
                "source": "collecte", "fiabilite": fiab}
    recent = bool(h) and h["date"] >= (datetime.strptime(date_max(produit)[:10], "%Y-%m-%d") - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
    return {**base, "prix": h["prix"], "date": h["date"], "marche": h["marche"], "type": h["type"], "flag": h["flag"],
            "source": "hdx", "fiabilite": "Indicatif" if recent else "Ancien"}


def date_max(produit):
    d = db().execute("SELECT MAX(date) FROM prices WHERE product=?", (produit,)).fetchone()[0]
    return d or datetime.now().strftime("%Y-%m-%d")   # produit ajouté sans relevé HDX


def calculer(d):
    produit, zone, qte = d["produit"], d["lieu"], float(d["quantite_kg"])
    offre, cout = d.get("prix_propose_kilo"), d.get("cout_revient_kilo")
    avert = []
    dmax = date_max(produit)
    limite = (datetime.strptime(dmax[:10], "%Y-%m-%d") - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
    crowd = crowd_all().get(produit, {})
    ref = resoudre(produit, zone, crowd)
    hist = historique(produit, zone, ref["date_hdx"] or ref["date"]) if ref else None
    if ref and ref["source"] == "hdx" and ref["date"] < limite:
        avert.append(f"Le dernier prix connu à {zone} date du {fmt_date(ref['date'])} : il peut être dépassé.")
    if not ref:
        avert.append(f"Je n'ai pas de prix de {label(produit)} à {zone} dans ma base.")

    verdict = None
    if offre and ref:
        ecart = (offre - ref["prix"]) / ref["prix"]
        niveau = "inférieure" if ecart < -SEUIL_VERDICT else "supérieure" if ecart > SEUIL_VERDICT else "proche"
        verdict = {"niveau": niveau, "ecart_pct": round(ecart * 100, 1),
                   "sous_minimum_12_mois": bool(hist and offre < hist["min"]),
                   "au_dessus_maximum_12_mois": bool(hist and offre > hist["max"])}

    options = []
    base_prix = offre if offre else (ref["prix"] if ref else None)
    if base_prix:
        options.append(_option(f"{zone} ({'offre reçue' if offre else 'prix observé'})", zone, base_prix,
                               ref["date"] if (ref and not offre) else None, ref["marche"] if (ref and not offre) else None,
                               0.0, qte, cout, offre=bool(offre)))
    zone_xy = coords(zone)
    if zone_xy is None:
        avert.append(f"Je ne connais pas les coordonnées de {zone} : je ne peux pas calculer le transport vers d'autres marchés.")
    else:
        zones = {r[0] for r in db().execute(
            "SELECT DISTINCT zone FROM prices WHERE product=? AND zone<>? AND date>=? AND unit IN (%s)" % ",".join("?" * len(UNITES_OK)),
            (produit, zone, limite, *UNITES_OK))}
        zones |= {z for z, c in crowd.items() if z != zone and c["n"] >= MIN_COLLECTE and (c.get("appareils", c["n"]) >= MIN_SANS_HDX or dernier_prix(produit, z))}
        for z in sorted(zones):
            xy = coords(z)
            if not xy:
                continue
            km = haversine(zone_xy, xy) * ROAD_FACTOR
            if km > MAX_DISTANCE_KM:
                continue
            p = resoudre(produit, z, crowd, depuis=limite)
            if p and p["fiabilite"] != "Peu de données":
                options.append(_option(z, z, p["prix"], p["date"], p["marche"], km, qte, cout, offre=False))
    options.sort(key=lambda o: o["net_total_fcfa"], reverse=True)
    return {"reference": ({"zone": zone, **ref} if ref else None), "historique_12_mois": hist, "verdict_offre": verdict,
            "options": options, "avertissements": avert, "resume": _resume(options)}


def _resume(options):
    """Le chiffre clé affiché en grand : meilleure option et gain (calculés ici, jamais dans le navigateur)."""
    if not options:
        return None
    meilleure = options[0]
    base = next((o for o in options if o["est_offre_recue"] or o["distance_km"] == 0), None)
    gain = meilleure["net_total_fcfa"] - base["net_total_fcfa"] if base and meilleure is not base else None
    ailleurs = bool(gain and gain > 0)
    return {"meilleure": meilleure["zone"], "distance_km": meilleure["distance_km"], "ailleurs": ailleurs,
            "gain_fcfa": gain if ailleurs else None, "base_est_offre": bool(base and base["est_offre_recue"]),
            "marge_offre_fcfa": base.get("marge_nette_fcfa") if base else None}


def _option(nom, zone, prix, date_, marche, km, qte, cout, offre):
    transport = km * TRANSPORT_FCFA_PAR_KG_KM * qte
    brut = prix * qte
    net = brut - transport
    o = {"marche": nom, "zone": zone, "est_offre_recue": offre, "prix_kg": round(prix, 1), "date_prix": date_,
         "marche_releve": marche, "distance_km": round(km), "transport_fcfa": round(transport),
         "revenu_brut_fcfa": round(brut), "net_total_fcfa": round(net), "net_par_kg": round(net / qte, 1)}
    if cout:
        o["marge_nette_fcfa"] = round(net - cout * qte)
        o["seuil_rentabilite_kg"] = round(cout + transport / qte, 1)
    return o


# ----------------------------------------------------------------------------- rédaction (chiffres issus du calcul, jamais d'un LLM)
def _type_prix(ref):
    t = (ref["type"] or "").lower()
    return "prix de détail" if t == "retail" else "prix de gros" if t == "wholesale" else "prix"


def rediger(d, res):
    """Renvoie (texte affiché, texte lu à voix haute). Le texte lu est court et sans symboles (F, %, /)."""
    produit, zone, qte = label(d["produit"]), d["lieu"], d["quantite_kg"]
    offre, cout = d.get("prix_propose_kilo"), d.get("cout_revient_kilo")
    L, V = [], []
    ref, v, opts = res["reference"], res["verdict_offre"], res["options"]
    L.append(f"Vente de {fmt(qte)} kg de {produit} à {zone}" + (f", offre reçue : {fmt(offre)} F/kg." if offre else "."))
    V.append(f"Pour {fmt(qte)} kilos de {produit} à {zone}.")
    if ref:
        if ref["source"] == "collecte":
            hdx = f" ; HDX : {fmt(ref['prix_hdx'])} F/kg, relevé du {fmt_date(ref['date_hdx'])}" if ref["prix_hdx"] else ""
            L.append(f"Prix de référence à {zone} : {fmt(ref['prix'])} F/kg ({_type_prix(ref)} collecté auprès de "
                     f"{ref['n_contrib']} personnes ces {JOURS_COLLECTE} derniers jours{hdx}).")
            V.append(f"Le prix de référence à {zone} est de {fmt(ref['prix'])} francs le kilo, d'après "
                     f"{ref['n_contrib']} relevés récents de producteurs et de commerçants.")
        else:
            L.append(f"Prix de référence à {zone} : {fmt(ref['prix'])} F/kg ({_type_prix(ref)}, marché de {ref['marche']}, "
                     f"relevé du {fmt_date(ref['date'])}, source : {SOURCE}).")
            V.append(f"Le prix de référence à {zone} est de {fmt(ref['prix'])} francs le kilo, "
                     f"{_type_prix(ref)} relevé en {date_parlee(ref['date'])}.")
            if ref["n_contrib"]:
                L.append(f"{ref['n_contrib']} contribution(s) récente(s) à {fmt(ref['prix_collecte'])} F/kg : pas encore assez "
                         f"pour faire référence (il en faut {MIN_COLLECTE}).")
    if v:
        e = virg(abs(v["ecart_pct"]))
        if v["niveau"] == "inférieure":
            tail = " et sous le minimum des 12 derniers mois" if v["sous_minimum_12_mois"] else ""
            L.append(f"Votre offre est inférieure de {e} % au prix de référence{tail}.")
            V.append(f"Votre offre est inférieure de {e} pour cent au prix de référence{tail}.")
        elif v["niveau"] == "supérieure":
            L.append(f"Votre offre est supérieure de {e} % au prix de référence.")
            V.append(f"Votre offre est supérieure de {e} pour cent au prix de référence.")
        else:
            s = "plus" if v["ecart_pct"] >= 0 else "moins"
            L.append(f"Votre offre est proche du prix de référence ({'+' if v['ecart_pct'] >= 0 else '-'}{e} %).")
            V.append(f"Votre offre est proche du prix de référence, {e} pour cent de {s}.")
    if cout and offre:
        marge = (offre - cout) * qte
        if marge >= 0:
            L.append(f"Avec votre coût de {fmt(cout)} F/kg, cette offre vous laisse {fmt(marge)} F de marge.")
            V.append(f"Avec votre coût de {fmt(cout)} francs le kilo, cette offre vous laisse {fmt(marge)} francs de marge.")
        else:
            L.append(f"Avec votre coût de {fmt(cout)} F/kg, cette offre vous ferait perdre {fmt(-marge)} F.")
            V.append(f"Avec votre coût de {fmt(cout)} francs le kilo, cette offre vous ferait perdre {fmt(-marge)} francs.")
    elif offre and not cout:
        L.append("Dites-moi votre coût de revient par kilo pour que je calcule votre marge.")
        V.append("Si vous me donnez votre coût de revient par kilo, je calcule aussi votre marge.")
    if opts:
        L.append("Options comparées (net = revenu moins transport estimé) :")
        for o in opts:
            ligne = f"- {o['marche']} : {fmt(o['prix_kg'])} F/kg"
            if o["distance_km"]:
                ligne += f", {o['distance_km']} km, transport ≈ {fmt(o['transport_fcfa'])} F"
            ligne += f", net {fmt(o['net_total_fcfa'])} F"
            if "marge_nette_fcfa" in o:
                ligne += f", marge {fmt(o['marge_nette_fcfa'])} F"
            if o["date_prix"]:
                ligne += f" (relevé du {fmt_date(o['date_prix'])})"
            L.append(ligne)
        rs = res["resume"]
        if rs["ailleurs"]:
            cible = "votre offre" if rs["base_est_offre"] else "la vente sur place"
            L.append(f"Le meilleur net est à {rs['meilleure']} : {fmt(rs['gain_fcfa'])} F de plus que {cible}, transport déduit.")
            V.append(f"Le meilleur net est à {rs['meilleure']}, à {rs['distance_km']} kilomètres : "
                     f"{fmt(rs['gain_fcfa'])} francs de plus que {cible}, transport déduit.")
        elif opts[0]["est_offre_recue"] or opts[0]["distance_km"] == 0:
            L.append("Après transport, la vente sur place est l'option la plus avantageuse parmi celles comparées.")
            V.append("Après transport, la vente sur place est la plus avantageuse parmi les marchés comparés.")
        else:
            L.append(f"L'option au meilleur net est {opts[0]['marche']}.")
            V.append(f"L'option au meilleur net est {opts[0]['marche']}.")
        L.append("Hypothèses : transport ≈ %s F par kg et par km (distance routière estimée), à ajuster selon votre situation. "
                 "La décision de vendre vous appartient." % virg(TRANSPORT_FCFA_PAR_KG_KM))
    for a in res["avertissements"]:
        L.append("Attention : " + a)
    if not ref and not opts:
        V.append("Je n'ai pas assez de prix pour vous conseiller. Voyez l'agent de la coopérative.")
    else:
        V.append("La décision de vendre vous appartient.")
    return "\n".join(L), " ".join(V)


# ----------------------------------------------------------------------------- orchestration (sans état serveur)
def _etat_propre(state):
    """Le navigateur renvoie l'état : on n'en garde que les champs connus, de types simples."""
    cur = {k: None for k in CLES}
    attendu = []
    if isinstance(state, dict):
        for k, v in (state.get("donnees") or {}).items():
            if k in CLES and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                cur[k] = v[:80] if isinstance(v, str) else v
        attendu = [a for a in (state.get("attendu") or []) if a in QUESTIONS]
    return cur, attendu


def conseiller(texte, state=None, extraction_llm=None):
    """Point d'entrée : `texte` déjà transcrit, `state` renvoyé par l'appel précédent.
    `extraction_llm` : dict (clés CLES) issu du LLM, ou None. Renvoie un dict JSON-sérialisable."""
    out = {"status": "ok", "transcription": texte, "questions": [], "notes": [], "state": None}
    if not texte or not texte.strip():
        return {**out, "status": "error", "reponse_texte": "Je n'ai rien entendu. Pouvez-vous répéter ?",
                "reponse_vocale": "Je n'ai rien entendu. Pouvez-vous répéter ?", "state": state}
    cur, attendu = _etat_propre(state)
    nouveau = fusionner(extraction_llm, extraire_regles(texte))
    out["extraction"] = "llm+regles" if extraction_llm else "regles"
    for k, v in nouveau.items():
        if v not in (None, ""):
            cur[k] = v
    for champ in attendu:  # réponse courte à une question précédente
        if not nouveau.get(champ):
            val = reponse_champ(champ, texte)
            if val:
                cur[champ] = val
    valide, manquants, notes = valider(cur)
    out["donnees"], out["notes"] = valide, notes
    out["produit_fr"] = label(valide["produit"]) if valide.get("produit") else None
    if manquants:
        out["status"] = "needs_info"
        out["questions"] = [QUESTIONS[m] for m in manquants]
        out["state"] = {"donnees": {**cur, **{k: valide[k] for k in ("produit", "lieu")}}, "attendu": manquants}
        out["reponse_texte"] = out["reponse_vocale"] = " ".join(notes + out["questions"][:1])  # une seule question à la fois, plus facile à l'oreille
    else:
        res = calculer(valide)
        texte_rep, vocal = rediger(valide, res)
        out.update(res)
        out["reponse_texte"], out["reponse_vocale"] = texte_rep, vocal
    return out
