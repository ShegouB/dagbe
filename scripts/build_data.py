"""Construit data/prices.db (SQLite compacte, lecture seule) à partir du CSV HDX HAPI des prix alimentaires du Bénin.

Usage :  python scripts/build_data.py chemin/vers/hdx_hapi_food_price_ben.csv [sortie.db]

On ne garde que ce dont le calcul a besoin : prix en kilogramme, 15 derniers mois (par rapport à la date la plus
récente du fichier). Le fichier produit (quelques Mo) est à versionner avec le projet pour le déploiement Vercel.
"""
import csv
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit(__doc__)
src = sys.argv[1]
dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "data" / "prices.db"
MOIS_GARDES = 15


def pick(r, *names):
    for n in names:
        if r.get(n) not in (None, ""):
            return r[n]
    return None


with open(src, newline="", encoding="utf-8-sig") as f:
    rows = [r for r in csv.DictReader(f) if not str(next(iter(r.values()), "")).startswith("#")]  # ligne de hashtags HDX
print("Lignes lues :", len(rows), "| colonnes :", list(rows[0].keys()) if rows else "aucune")

lignes = []
for r in rows:
    price, produit, unite = pick(r, "price"), pick(r, "commodity_name"), pick(r, "unit")
    zone = pick(r, "admin2_name", "provider_admin2_name", "admin1_name", "provider_admin1_name")
    date = (pick(r, "reference_period_end", "reference_period_start") or "")[:10]
    if price is None or not produit or not zone or not date or (unite or "").lower() != "kg":
        continue
    lat, lon = pick(r, "lat"), pick(r, "lon")
    lignes.append((produit, zone, pick(r, "market_name"), "KG", pick(r, "currency_code"), float(price),
                   pick(r, "price_type"), date, pick(r, "price_flag"),
                   float(lat) if lat else None, float(lon) if lon else None))
if not lignes:
    sys.exit("Aucune ligne exploitable : vérifiez les noms de colonnes ci-dessus.")

dmax = max(l[7] for l in lignes)
debut = (datetime.strptime(dmax, "%Y-%m-%d") - timedelta(days=30 * MOIS_GARDES)).strftime("%Y-%m-%d")
lignes = [l for l in lignes if l[7] >= debut]

dst.parent.mkdir(parents=True, exist_ok=True)
dst.unlink(missing_ok=True)
db = sqlite3.connect(dst)
db.execute("CREATE TABLE prices(product TEXT, zone TEXT, market TEXT, unit TEXT, currency TEXT, price REAL, "
           "price_type TEXT, date TEXT, price_flag TEXT, lat REAL, lon REAL)")
db.executemany("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?,?)", lignes)
db.execute("CREATE INDEX ix_pz ON prices(product, zone, date)")
db.execute("CREATE INDEX ix_z ON prices(zone)")
db.commit()
db.execute("VACUUM")
db.close()

c = sqlite3.connect(dst)
print(f"{len(lignes)} prix gardés ({debut} -> {dmax}) dans {dst} ({dst.stat().st_size // 1024} Ko)")
print("produits :", [r[0] for r in c.execute("SELECT DISTINCT product FROM prices ORDER BY 1")])
print("zones :", c.execute("SELECT COUNT(DISTINCT zone) FROM prices").fetchone()[0],
      "| avec coordonnées :", c.execute("SELECT COUNT(DISTINCT zone) FROM prices WHERE lat IS NOT NULL").fetchone()[0])
print("types de prix :", c.execute("SELECT price_type, COUNT(*) FROM prices GROUP BY 1").fetchall())
