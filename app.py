"""AgriPrix Voice — API FastAPI (point d'entrée Vercel : `app`).

  POST /api/advice   voix (audio) ou texte  -> transcription -> extraction -> calcul -> réponse
  POST /api/tts      texte -> audio WAV (Gemini, palier gratuit) ; en cas d'échec le navigateur lit lui-même
  GET  /api/health   état des clés et de la base

Aucun état n'est gardé sur le serveur (Vercel) : l'état du dialogue circule dans `state`.
"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agriprix import core, services

app = FastAPI(title="AgriPrix Voice")
PUBLIC = Path(__file__).parent / "public"
MAX_AUDIO = 4_000_000  # Vercel limite le corps d'une requête à 4,5 Mo


def _erreur(message, code=200, **extra):
    """Erreur lisible ET dite à voix haute par le front (reponse_vocale)."""
    return JSONResponse({"status": "error", "reponse_texte": message, "reponse_vocale": message, **extra}, status_code=code)


def _vocabulaire():
    try:
        _, zones = core.load_lists()
        return ("Vocabulaire : " + ", ".join(sorted(set(core.LABEL_FR.values())) + sorted(zones)[:40])
                + ". Prix en francs CFA le kilo.")
    except Exception:
        return ""


@app.post("/api/advice")
async def advice(text: str = Form(None), state: str = Form(None), audio: UploadFile = File(None)):
    try:
        etat = json.loads(state) if state else None
    except ValueError:
        etat = None
    try:
        if audio is not None:
            data = await audio.read()
            if len(data) > MAX_AUDIO:
                return _erreur("L'enregistrement est trop long. Parlez plus brièvement.")
            if len(data) < 1500:
                return _erreur("Je n'ai rien entendu. Appuyez sur le micro et parlez.", state=etat)
            try:
                text = services.transcrire(data, audio.filename or "audio.webm", _vocabulaire())
            except services.ServiceIndisponible as e:
                return _erreur("Je n'arrive pas à comprendre l'enregistrement pour le moment. Vous pouvez écrire votre demande.",
                               detail=str(e), state=etat)
        text = (text or "").strip()[:600]
        llm = services.extraire_llm(text) if text else None
        return core.conseiller(text, etat, llm)
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


@app.get("/api/health")
def health():
    try:
        produits, zones = core.load_lists()
        base = {"ok": True, "produits": len(produits), "zones": len(zones)}
    except Exception as e:
        base = {"ok": False, "detail": str(e)}
    return {"base": base, "asr": bool(os.getenv("GROQ_API_KEY")), "llm": bool(os.getenv("GEMINI_API_KEY")),
            "tts": bool(os.getenv("GEMINI_API_KEY")),
            "hypotheses": {"transport_fcfa_par_kg_km": core.TRANSPORT_FCFA_PAR_KG_KM, "distance_max_km": core.MAX_DISTANCE_KM}}


# Fichiers de l'interface (en local ; sur Vercel, public/ est servi directement par le CDN). Monté en dernier : l'API a priorité.
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
