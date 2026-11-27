"""Décisions de l'administrateur sur les produits et marchés proposés par les contributeurs.

Utilisation (avec DATABASE_URL pointant vers la base Neon) :

    python -m agriprix.admin attente                       # ce qui attend une décision
    python -m agriprix.admin produit "Piment"              # accepter un nouveau produit
    python -m agriprix.admin zone "Dassa-Zoumè" 7.75 2.18  # accepter un nouveau marché (avec ses coordonnées GPS)
    python -m agriprix.admin renommer produit "piment rouge" "Piment"   # regrouper des variantes avant d'accepter
    python -m agriprix.admin rejeter produit "n'importe quoi"            # écarter une proposition

Dès que le produit ET le marché d'une contribution sont connus, elle est activée (après le même contrôle de
plausibilité que les autres contributions). Un marché sans coordonnées ne peut pas être accepté : sans elles, on ne
peut pas calculer le transport vers les autres marchés.
"""
import statistics
import sys

from . import api, core, store


def _cle(x):
    return core.norm(x)


def promouvoir():
    """Active les contributions en attente dont le produit et le marché sont désormais connus. Renvoie leur nombre."""
    core.recharger_extras()
    produits, zones = core.load_lists()
    idx_p = {_cle(p): p for p in produits}
    idx_z = {_cle(z): z for z in zones}
    n = 0
    for ident, produit, zone, prix, _type, _date in store.en_attente():
        p, z = idx_p.get(_cle(produit)), idx_z.get(_cle(zone))
        if not (p and z):
            continue
        ref = core.dernier_prix(p, z)
        ok = not ref or (api.RATIO_MIN <= prix / ref["prix"] <= api.RATIO_MAX)
        store.maj_contribution(ident, statut=store.CRITERE_OK if ok else store.CRITERE_A_VERIFIER, produit=p, zone=z)
        n += 1
    return n


def attente():
    lignes = store.en_attente()
    groupes = {}
    for _id, p, z, prix, _t, _d in lignes:
        groupes.setdefault((_cle(p), _cle(z)), {"p": p, "z": z, "prix": []})["prix"].append(prix)
    produits, zones = core.load_lists()
    connus_p, connus_z = {_cle(x) for x in produits}, {_cle(x) for x in zones}
    if not groupes:
        print("Rien en attente.")
    for g in sorted(groupes.values(), key=lambda g: -len(g["prix"])):
        manque = [m for m, ok in (("produit", _cle(g["p"]) in connus_p), ("marché", _cle(g["z"]) in connus_z)) if not ok]
        print(f"{len(g['prix']):>3} contribution(s) | produit « {g['p']} » | marché « {g['z']} » | "
              f"médiane {round(statistics.median(g['prix']))} F/kg | à valider : {', '.join(manque) or 'rien (déjà connu)'}")


def main(argv):
    if not store.configure():
        sys.exit("DATABASE_URL absente : indiquez l'adresse de la base Neon.")
    cmd = argv[0] if argv else ""
    if cmd == "attente":
        attente()
    elif cmd == "produit" and len(argv) == 2:
        print("Produit ajouté." if store.ajouter_ajout("produit", argv[1].strip()) else "Ce produit existe déjà.")
        print(promouvoir(), "contribution(s) activée(s).")
    elif cmd == "zone" and len(argv) == 4:
        lat, lon = float(argv[2].replace(",", ".")), float(argv[3].replace(",", "."))
        if not (5.5 <= lat <= 12.5 and 0.5 <= lon <= 4.0):
            sys.exit("Coordonnées hors du Bénin : vérifiez latitude et longitude.")
        print("Marché ajouté." if store.ajouter_ajout("zone", argv[1].strip(), lat, lon) else "Ce marché existe déjà.")
        print(promouvoir(), "contribution(s) activée(s).")
    elif cmd in ("renommer", "rejeter") and len(argv) == (4 if cmd == "renommer" else 3) and argv[1] in ("produit", "zone"):
        champ, ancien = argv[1], argv[2]
        n = 0
        for ident, p, z, *_ in store.en_attente():
            if _cle(p if champ == "produit" else z) == _cle(ancien):
                if cmd == "rejeter":
                    store.maj_contribution(ident, statut=store.CRITERE_REJETE)
                elif champ == "produit":
                    store.maj_contribution(ident, produit=argv[3].strip())
                else:
                    store.maj_contribution(ident, zone=argv[3].strip())
                n += 1
        print(n, "contribution(s) modifiée(s).")
        if cmd == "renommer":
            print(promouvoir(), "contribution(s) activée(s).")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
