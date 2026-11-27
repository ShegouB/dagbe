"""Stockage des contributions de prix (volontaires).

Sur Vercel le disque est en lecture seule : les contributions vont dans une base Postgres (Neon, gratuite, via
Vercel). Variable d'environnement : DATABASE_URL (ou POSTGRES_URL). Pour le développement et les tests, une URL
`sqlite:///chemin.db` fonctionne aussi.

Principe de résilience : si la base est absente ou en panne, `StoreIndisponible` est levée et l'application
continue de répondre avec la copie HDX embarquée (data/prices.db).
"""
import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone

CRITERE_OK = "ok"
CRITERE_A_VERIFIER = "a_verifier"
CRITERE_ATTENTE = "en_attente"   # produit ou marché inconnu : attend la décision de l'administrateur
CRITERE_REJETE = "rejete"


class StoreIndisponible(Exception):
    pass


def _url():
    return os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""


def configure():
    return bool(_url())


def maintenant():
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def hash_ip(ip):
    sel = os.getenv("IP_SALT", "dagbe")
    return hashlib.sha256((sel + str(ip or "?")).encode()).hexdigest()[:16]


_pret = set()


def _connexion():
    url = _url()
    if not url:
        raise StoreIndisponible("DATABASE_URL absente")
    try:
        if url.startswith("sqlite"):
            c = sqlite3.connect(url.split("///", 1)[1], timeout=5)
            ph, serial, reel = "?", "INTEGER PRIMARY KEY AUTOINCREMENT", "REAL"
        else:
            import psycopg  # import tardif : inutile en local sans Postgres

            c = psycopg.connect(url, connect_timeout=5)
            ph, serial, reel = "%s", "BIGSERIAL PRIMARY KEY", "DOUBLE PRECISION"
        if url not in _pret:
            c.execute(f"CREATE TABLE IF NOT EXISTS contributions(id {serial}, product TEXT NOT NULL, zone TEXT NOT NULL, "
                      f"price {reel} NOT NULL, price_type TEXT NOT NULL, device TEXT NOT NULL, ip_hash TEXT NOT NULL, "
                      f"status TEXT NOT NULL, created_at TEXT NOT NULL)")
            c.execute(f"CREATE TABLE IF NOT EXISTS ajouts(id {serial}, kind TEXT NOT NULL, name TEXT NOT NULL, "
                      f"lat {reel}, lon {reel}, created_at TEXT NOT NULL)")
            c.commit()
            _pret.add(url)
        return c, ph
    except StoreIndisponible:
        raise
    except Exception as e:
        raise StoreIndisponible(type(e).__name__) from e


def _executer(sql_fn, *args):
    c = None
    try:
        c, ph = _connexion()
        return sql_fn(c, ph, *args)
    except StoreIndisponible:
        raise
    except Exception as e:
        raise StoreIndisponible(type(e).__name__) from e
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def ajouter(produit, zone, prix, type_prix, device, ip_hash, statut):
    def f(c, ph):
        c.execute(f"INSERT INTO contributions(product,zone,price,price_type,device,ip_hash,status,created_at) "
                  f"VALUES({','.join([ph] * 8)})", (produit, zone, float(prix), type_prix, device, ip_hash, statut, iso(maintenant())))
        c.commit()
    _executer(f)


def compter_24h(device, ip_hash):
    """(contributions de cet appareil, de cette adresse) sur les dernières 24 h."""
    depuis = iso(maintenant() - timedelta(hours=24))

    def f(c, ph):
        n1 = c.execute(f"SELECT COUNT(*) FROM contributions WHERE device={ph} AND created_at>={ph}", (device, depuis)).fetchone()[0]
        n2 = c.execute(f"SELECT COUNT(*) FROM contributions WHERE ip_hash={ph} AND created_at>={ph}", (ip_hash, depuis)).fetchone()[0]
        return n1, n2
    return _executer(f)


def recentes(jours=14):
    """Contributions retenues (status ok) des `jours` derniers jours : [(produit, zone, prix, type, date_iso, appareil)]."""
    depuis = iso(maintenant() - timedelta(days=jours))

    def f(c, ph):
        return c.execute(f"SELECT product, zone, price, price_type, created_at, device FROM contributions "
                         f"WHERE status={ph} AND created_at>={ph}", (CRITERE_OK, depuis)).fetchall()
    return _executer(f)


# ----------------------------------------------------------------------------- produits et marchés ajoutés par l'administrateur
def lire_ajouts():
    """[(kind, nom, lat, lon)] : kind = 'produit' ou 'zone'."""
    def f(c, ph):
        return c.execute("SELECT kind, name, lat, lon FROM ajouts ORDER BY id").fetchall()
    return _executer(f)


def ajouter_ajout(kind, nom, lat=None, lon=None):
    def f(c, ph):
        if c.execute(f"SELECT 1 FROM ajouts WHERE kind={ph} AND name={ph}", (kind, nom)).fetchone():
            return False
        c.execute(f"INSERT INTO ajouts(kind,name,lat,lon,created_at) VALUES({','.join([ph] * 5)})",
                  (kind, nom, lat, lon, iso(maintenant())))
        c.commit()
        return True
    return _executer(f)


def en_attente():
    """Contributions en attente : [(id, produit, zone, prix, type, date_iso)]."""
    def f(c, ph):
        return c.execute(f"SELECT id, product, zone, price, price_type, created_at FROM contributions "
                         f"WHERE status={ph} ORDER BY id", (CRITERE_ATTENTE,)).fetchall()
    return _executer(f)


def maj_contribution(ident, statut=None, produit=None, zone=None):
    def f(c, ph):
        if statut:
            c.execute(f"UPDATE contributions SET status={ph} WHERE id={ph}", (statut, ident))
        if produit:
            c.execute(f"UPDATE contributions SET product={ph} WHERE id={ph}", (produit, ident))
        if zone:
            c.execute(f"UPDATE contributions SET zone={ph} WHERE id={ph}", (zone, ident))
        c.commit()
    _executer(f)
