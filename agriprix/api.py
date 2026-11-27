"""Logique des endpoints « prix » et « contributions » (fonctions pures, sans FastAPI : testables hors ligne)."""
import re
import statistics

from . import core, store

MAX_PAR_APPAREIL = 10    # contributions par appareil et par 24 h
MAX_PAR_ADRESSE = 30     # contributions par adresse réseau et par 24 h
PRIX_MIN, PRIX_MAX = 10, 50000      # F/kg : en dehors, c'est une faute de frappe
RATIO_MIN, RATIO_MAX = 0.4, 2.5     # plage de plausibilité par rapport au dernier prix HDX


def zones_et_produits():
    produits, zones = core.load_lists()
    return {
        "zones": [{"nom": z, "coords": core.coords(z) is not None} for z in sorted(zones, key=core.norm)],
        "produits": sorted(({"id": p, "fr": core.label(p)} for p in produits), key=lambda x: core.norm(x["fr"])),
        "collecte": store.configure(),
    }


def _item(produit, zone, crowd_produit):
    r = core.resoudre(produit, zone, crowd_produit)
    if not r:
        return None
    h = core.historique(produit, zone, r["date_hdx"] or r["date"])
    return {"produit": produit, "produit_fr": core.label(produit), "prix": round(r["prix"]), "type": r["type"], "date": r["date"],
            "source": r["source"], "fiabilite": r["fiabilite"], "n_contrib": r["n_contrib"],
            "prix_hdx": round(r["prix_hdx"]) if r["prix_hdx"] else None, "date_hdx": r["date_hdx"],
            "prix_collecte": round(r["prix_collecte"]) if r["prix_collecte"] else None,
            "min_12_mois": round(h["min"]) if h else None, "max_12_mois": round(h["max"]) if h else None}


def prix_zone(zone, produit=None):
    """Tous les prix connus d'une zone (ou d'un seul produit, avec les marchés voisins)."""
    produits, zones = core.load_lists()
    zone = core.match_zone(zone) if zone not in zones else zone
    if not zone:
        return {"erreur": "zone_inconnue"}
    if produit and produit not in produits:
        return {"erreur": "produit_inconnu"}
    tout = core.crowd_all()
    cibles = [produit] if produit else list(produits)
    items = [i for p in cibles if (i := _item(p, zone, tout.get(p, {})))]
    items.sort(key=lambda i: core.norm(i["produit_fr"]))
    out = {"zone": zone, "items": items, "voisins": []}
    xy = core.coords(zone)
    if produit and xy:
        crowd = tout.get(produit, {})
        candidats = {z for z in zones if z != zone} | set(crowd)
        for z in candidats:
            c = core.coords(z)
            if not c:
                continue
            km = core.haversine(xy, c) * core.ROAD_FACTOR
            if km > core.MAX_DISTANCE_KM:
                continue
            i = _item(produit, z, crowd)
            if i and i["fiabilite"] not in ("Peu de données", "Ancien"):
                out["voisins"].append({"zone": z, "distance_km": round(km), "prix": i["prix"], "source": i["source"],
                                       "fiabilite": i["fiabilite"], "date": i["date"]})
        out["voisins"].sort(key=lambda v: v["distance_km"])
        out["voisins"] = out["voisins"][:8]
    return out


def contribuer(data, ip):
    """Enregistre une contribution. Renvoie (code HTTP, réponse). Rien n'est accepté sans contrôle."""
    produits, zones = core.load_lists()
    produit, zone = data.get("produit"), data.get("zone")
    if produit not in produits or zone not in zones:
        return 422, {"ok": False, "message": "Produit ou marché inconnu."}
    try:
        prix = float(str(data.get("prix")).replace(",", ".").replace(" ", ""))
    except ValueError:
        prix = 0
    if not (PRIX_MIN <= prix <= PRIX_MAX):
        return 422, {"ok": False, "message": f"Le prix doit être un nombre de francs par kilo (entre {PRIX_MIN} et {PRIX_MAX})."}
    type_prix = "Wholesale" if data.get("type") == "Wholesale" else "Retail"
    device = re.sub(r"[^A-Za-z0-9-]", "", str(data.get("device") or ""))[:64]
    if len(device) < 8:
        return 422, {"ok": False, "message": "Appareil non reconnu. Rechargez la page."}
    if not store.configure():
        return 503, {"ok": False, "message": "La collecte n'est pas encore activée sur ce serveur."}
    ip_hash = store.hash_ip(ip)
    try:
        n_dev, n_ip = store.compter_24h(device, ip_hash)
        if n_dev >= MAX_PAR_APPAREIL or n_ip >= MAX_PAR_ADRESSE:
            return 429, {"ok": False, "message": "Merci ! Vous avez atteint la limite de contributions pour aujourd'hui."}
        ref = core.dernier_prix(produit, zone)
        plausible = True
        if ref and not (RATIO_MIN <= prix / ref["prix"] <= RATIO_MAX):
            plausible = False
        store.ajouter(produit, zone, prix, type_prix, device, ip_hash,
                      store.CRITERE_OK if plausible else store.CRITERE_A_VERIFIER)
    except store.StoreIndisponible:
        return 503, {"ok": False, "message": "Le service de collecte est momentanément indisponible. Réessayez plus tard."}
    if plausible:
        return 200, {"ok": True, "statut": "ok", "message": "Merci ! Votre prix sera pris en compte avec les autres contributions."}
    return 200, {"ok": True, "statut": "a_verifier",
                 "message": "Merci. Ce prix est très éloigné des relevés habituels : il est enregistré mais ne sera pas utilisé tant qu'il n'est pas confirmé."}


_PRIX_SEUL = re.compile(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(?:f\b|fcfa|francs?)", re.I)


def extraire_contribution(texte, extraction_llm=None):
    """Champs d'un formulaire à partir d'une phrase (« le maïs est à 180 francs au marché de Dantokpa »).
    Sert seulement à PRÉ-REMPLIR : l'utilisateur confirme avant l'envoi."""
    r = core.fusionner(extraction_llm, core.extraire_regles(texte))
    prix = r.get("prix_propose_kilo")
    if not prix:
        m = _PRIX_SEUL.search(texte.replace("\xa0", " ").replace(" ", " "))
        prix = core.parse_nombre(m.group(1)) if m else None
    produit = core.match_product(r["produit"]) if r.get("produit") else None
    zone = core.match_zone(r["lieu"]) if r.get("lieu") else None
    return {"transcription": texte, "produit": produit, "produit_fr": core.label(produit) if produit else None,
            "zone": zone, "prix": prix}
