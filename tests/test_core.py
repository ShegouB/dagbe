"""Tests hors ligne (aucune clé, aucun réseau) : python -m pytest -q   ou   python tests/test_core.py"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PRICES_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
from agriprix import core  # noqa: E402

core.DB_PATH = os.environ["PRICES_DB"]


def fabrique():
    c = sqlite3.connect(core.DB_PATH)
    c.execute("CREATE TABLE prices(product,zone,market,unit,currency,price,price_type,date,price_flag,lat,lon)")
    zones = {"Allada": (6.66, 2.15, 150), "Cotonou": (6.37, 2.43, 180), "Bohicon": (7.18, 2.07, 130),
             "Glazoue": (7.97, 2.23, 140), "Parakou": (9.34, 2.63, 190)}
    ins = "INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?,?)"
    for z, (la, lo, p) in zones.items():
        for d, f in [("2026-05-31", .9), ("2026-06-30", .95), ("2026-07-31", 1.0), ("2026-08-31", 1.0), ("2026-09-30", 1.02), ("2026-10-31", 1.0)]:
            c.execute(ins, ("Maize (white)", z, z, "KG", "XOF", p * f, "Retail", d, "actual", la, lo))
            c.execute(ins, ("Maize (white)", z, z, "KG", "XOF", p * f * .7, "Wholesale", d, "actual", la, lo))
        c.execute(ins, ("Tomatoes", z, z, "KG", "XOF", 400, "Retail", "2026-10-31", "actual", la, lo))
    c.execute(ins, ("Maize (white)", "Vieux", "Vieux", "KG", "XOF", 500, "Retail", "2026-01-31", "actual", 6.7, 2.2))
    c.commit()
    c.close()


fabrique()
core.reset_db()


def test_phrase_complete():
    phrase = ("ok j'ai reçu une offre pour vendre mon maïs à 100 francs le kilo sur le marché d'alada en fait j'ai produit 1000 kg de maïs")
    r = core.conseiller(phrase)
    d = r["donnees"]
    assert r["status"] == "ok"
    assert (d["produit"], d["lieu"], d["quantite_kg"], d["prix_propose_kilo"]) == ("Maize (white)", "Allada", 1000, 100)
    assert r["reference"]["prix"] == 150.0  # détail 'actual' le plus récent
    assert r["verdict_offre"]["niveau"] == "inférieure" and r["verdict_offre"]["ecart_pct"] == -33.3
    km = core.haversine((6.66, 2.15), (6.37, 2.43)) * 1.3
    oc = next(o for o in r["options"] if o["zone"] == "Cotonou")
    assert abs(oc["net_total_fcfa"] - (180 * 1000 - km * 0.15 * 1000)) < 1
    assert r["options"][0]["zone"] != "Allada"
    assert all(o["zone"] not in ("Parakou", "Vieux") for o in r["options"])  # trop loin / prix périmé


def test_resume_chiffre_cle():
    r = core.conseiller("1000 kg de maïs à Allada, offre 100 francs le kilo")
    rs = r["resume"]
    base = next(o for o in r["options"] if o["est_offre_recue"])
    assert rs["ailleurs"] and rs["meilleure"] == r["options"][0]["zone"]
    assert rs["gain_fcfa"] == r["options"][0]["net_total_fcfa"] - base["net_total_fcfa"] > 0
    assert r["produit_fr"] == "maïs blanc"


def test_voix_sans_symboles():
    r = core.conseiller("500 kg de maïs à Allada, on m'offre 100 francs le kilo, ça me coûte 110 francs le kilo à produire")
    v = r["reponse_vocale"]
    assert not any(s in v for s in ("%", " F ", "F/kg", "/", "≈"))
    assert "francs" in v and "pour cent" in v and "octobre 2026" in v and "décision" in v


def test_infos_manquantes_et_etat():
    r = core.conseiller("on me propose 120 francs le kilo de maïs")
    assert r["status"] == "needs_info" and core.QUESTIONS["lieu"] in r["questions"] and core.QUESTIONS["quantite_kg"] in r["questions"]
    r = core.conseiller("Allada", r["state"])  # l'état voyage dans le navigateur
    assert r["questions"] == [core.QUESTIONS["quantite_kg"]]
    r = core.conseiller("2 tonnes", r["state"])
    assert r["status"] == "ok" and r["donnees"]["quantite_kg"] == 2000 and r["donnees"]["prix_propose_kilo"] == 120
    assert r["state"] is None


def test_marge_et_perte():
    r = core.conseiller("j'ai 500 kg de maïs à Allada, on m'offre 100 francs le kilo, ça me coûte 110 francs le kilo à produire")
    base = next(o for o in r["options"] if o["est_offre_recue"])
    assert r["donnees"]["cout_revient_kilo"] == 110 and base["marge_nette_fcfa"] == -5000
    assert "perdre" in r["reponse_texte"]


def test_aveu_ignorance():
    r = core.conseiller("je veux vendre 300 kg de bitcoin à Paris à 50 francs le kilo")
    assert r["status"] == "needs_info" and core.QUESTIONS["produit"] in r["questions"] and core.QUESTIONS["lieu"] in r["questions"]
    assert core.match_zone("je veux vendre comme avant") is None
    assert core.match_zone("Alada") == "Allada"


def test_llm_ne_peut_pas_inventer_n_importe_quoi():
    llm = {"produit": "maïs", "quantite_kg": "-5", "lieu": "Allada", "prix_propose_kilo": "beaucoup", "cout_revient_kilo": None}
    r = core.conseiller("maïs à Allada", None, llm)
    assert r["status"] == "needs_info" and core.QUESTIONS["quantite_kg"] in r["questions"]  # nombre négatif refusé
    r = core.conseiller("maïs à Allada", {"donnees": {"produit": 5, "x": "y"}, "attendu": ["nimporte"]})  # état falsifié
    assert r["status"] == "needs_info"


def test_sans_offre_et_texte_vide():
    r = core.conseiller("je veux connaître le prix du maïs à Allada pour 1000 kg")
    assert r["status"] == "ok" and r["verdict_offre"] is None and r["options"]
    assert core.conseiller("")["status"] == "error"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
