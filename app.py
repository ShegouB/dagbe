"""Dagbé — API FastAPI (point d'entrée Vercel : `app`).

  POST /api/advice       voix (audio) ou texte  -> transcription -> extraction -> calcul -> réponse
  POST /api/tts          texte -> audio WAV (Gemini, palier gratuit) ; en cas d'échec le navigateur lit lui-même
  GET  /api/zones        zones et produits connus (listes de choix)
  GET  /api/prix         prix d'une zone (HDX croisé avec les contributions)
  POST /api/contribuer   un prix donné volontairement
  POST /api/extraire     voix/texte -> champs pour pré-remplir le formulaire de contribution
  GET  /api/health       état des clés et des bases

Aucun état n'est gardé sur le serveur (Vercel) : l'état du dialogue circule dans `state`.
"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agriprix import api, core, services, store

app = FastAPI(title="Dagbé")
PUBLIC = Path(__file__).parent / "public"
MAX_AUDIO = 4_000_000  # Vercel limite le corps d'une requête à 4,5 Mo


def _erreur(message, code=200, **extra):
    """Erreur lisible ET dite à voix haute par le front (reponse_vocale)."""
    return JSONResponse({"status": "error", "reponse_texte": message, "reponse_vocale": message, **extra}, status_code=code)


def _ip(request: Request):
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()


def _vocabulaire():
    try:
        _, zones = core.load_lists()
        return ("Vocabulaire : " + ", ".join(sorted(set(core.LABEL_FR.values())) + sorted(zones)[:40])
                + ". Prix en francs CFA le kilo.")
    except Exception:
        return ""


async def _texte_de(audio, text):
    """Texte de la demande : transcription de l'audio, ou texte saisi."""
    if audio is not None:
        data = await audio.read()
        if len(data) > MAX_AUDIO:
            raise ValueError("L'enregistrement est trop long. Parlez plus brièvement.")
        if len(data) < 1500:
            raise ValueError("Je n'ai rien entendu. Appuyez sur le micro et parlez.")
        return services.transcrire(data, audio.filename or "audio.webm", _vocabulaire())
    return (text or "").strip()[:600]


@app.post("/api/advice")
async def advice(text: str = Form(None), state: str = Form(None), audio: UploadFile = File(None)):
    try:
        etat = json.loads(state) if state else None
    except ValueError:
        etat = None
    try:
        try:
            texte = (await _texte_de(audio, text))[:600]
        except ValueError as e:
            return _erreur(str(e), state=etat)
        except services.ServiceIndisponible as e:
            return _erreur("Je n'arrive pas à comprendre l'enregistrement pour le moment. Vous pouvez écrire votre demande.",
                           detail=str(e), state=etat)
        llm = services.extraire_llm(texte) if texte else None
        return core.conseiller(texte, etat, llm)
    except FileNotFoundError as e:
        return _erreur("La base de prix n'est pas installée sur ce serveur.", detail=str(e))
    except Exception as e:  # on ne laisse jamais le front sans réponse parlable
        return _erreur("Une erreur est survenue. Réessayez ou voyez l'agent de la coopérative.", detail=type(e).__name__, state=etat)


@app.post("/api/tts")
async def tts(text: str = Form(...)):
    text = text.strip()[:900]
    if not text:
        return Response(status_code=400)
    try:
        return Response(services.synthese_wav(text), media_type="audio/wav", headers={"Cache-Control": "no-store"})
    except services.ServiceIndisponible as e:
        return JSONResponse({"error": "tts_indisponible", "detail": str(e)}, status_code=503)


@app.get("/api/zones")
def zones():
    try:
        return api.zones_et_produits()
    except FileNotFoundError:
        return JSONResponse({"erreur": "base_absente"}, status_code=503)


@app.get("/api/prix")
def prix(zone: str, produit: str = None):
    try:
        r = api.prix_zone(zone, produit)
    except FileNotFoundError:
        return JSONResponse({"erreur": "base_absente"}, status_code=503)
    return JSONResponse(r, status_code=404 if r.get("erreur") else 200)


@app.post("/api/contribuer")
async def contribuer(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return JSONResponse({"ok": False, "message": "Requête invalide."}, status_code=400)
    code, rep = api.contribuer(data, _ip(request))
    return JSONResponse(rep, status_code=code)


@app.post("/api/extraire")
async def extraire(text: str = Form(None), audio: UploadFile = File(None)):
    try:
        texte = await _texte_de(audio, text)
    except ValueError as e:
        return JSONResponse({"erreur": str(e)}, status_code=422)
    except services.ServiceIndisponible:
        return JSONResponse({"erreur": "Je n'arrive pas à comprendre l'enregistrement. Remplissez le formulaire."}, status_code=503)
    if not texte:
        return JSONResponse({"erreur": "Je n'ai rien entendu."}, status_code=422)
    return api.extraire_contribution(texte, services.extraire_llm(texte))


@app.get("/api/health")
def health():
    try:
        produits, zones_ = core.load_lists()
        base = {"ok": True, "produits": len(produits), "zones": len(zones_)}
    except Exception as e:
        base = {"ok": False, "detail": str(e)}
    collecte = {"configuree": store.configure(), "ok": False}
    if collecte["configuree"]:
        try:
            store.recentes(1)
            collecte["ok"] = True
        except store.StoreIndisponible as e:
            collecte["detail"] = str(e)
    return {"base": base, "collecte": collecte, "asr": bool(os.getenv("GROQ_API_KEY")), "llm": bool(os.getenv("GEMINI_API_KEY")),
            "tts": bool(os.getenv("GEMINI_API_KEY")),
            "hypotheses": {"transport_fcfa_par_kg_km": core.TRANSPORT_FCFA_PAR_KG_KM, "distance_max_km": core.MAX_DISTANCE_KM}}


# Fichiers de l'interface (en local ; sur Vercel, public/ est servi directement par le CDN). Monté en dernier : l'API a priorité.
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
