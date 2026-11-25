"""Appels aux services externes, en HTTP simple (pas de SDK lourd : déploiement Vercel léger).

  - transcrire()      Whisper large-v3 via Groq            (GROQ_API_KEY)
  - extraire_llm()    extraction JSON via Gemini            (GEMINI_API_KEY)
  - synthese_wav()    voix via Gemini TTS (palier gratuit)  (GEMINI_API_KEY)

Les clés ne sont lues que dans les variables d'environnement, jamais écrites dans le code.
"""
import base64
import io
import json
import os
import re
import wave

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def _liste(nom, defaut):
    return [m.strip() for m in os.getenv(nom, defaut).split(",") if m.strip()]


# Plusieurs modèles possibles : le suivant est essayé si le premier est refusé (nom inconnu, quota...).
def modeles_llm():
    return _liste("GEMINI_MODEL", "gemini-3.5-flash-lite,gemini-2.5-flash-lite")


def modeles_tts():
    return _liste("TTS_MODELS", "gemini-3.1-flash-tts-preview,gemini-2.5-flash-preview-tts")


def voix_tts():
    return os.getenv("TTS_VOICE", "Kore")


class ServiceIndisponible(Exception):
    pass


# ----------------------------------------------------------------------------- transcription
AUDIO_OK = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}


def transcrire(audio_bytes, filename="audio.webm", vocabulaire=""):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise ServiceIndisponible("GROQ_API_KEY manquante")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in AUDIO_OK:
        filename = "audio.webm"
    data = {"model": "whisper-large-v3", "language": "fr", "response_format": "json", "temperature": "0"}
    if vocabulaire:
        data["prompt"] = vocabulaire[:800]
    r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"},
                      files={"file": (filename, audio_bytes)}, data=data, timeout=45)
    if r.status_code != 200:
        raise ServiceIndisponible(f"Whisper {r.status_code}")
    return (r.json().get("text") or "").strip()


# ----------------------------------------------------------------------------- extraction
CONSIGNE = """Vous extrayez des données d'une demande vocale d'un producteur agricole béninois (français oral, parfois mal transcrit).
Répondez UNIQUEMENT par un objet JSON avec exactement ces clés :
- produit : nom de la culture ou du produit (texte tel que dit), sinon null
- quantite_kg : quantité en kilogrammes (nombre ; convertir les tonnes en kg), sinon null
- lieu : lieu ou marché où se trouve l'offre / le producteur, sinon null
- prix_propose_kilo : prix par kilo proposé au producteur en FCFA (nombre), sinon null
- cout_revient_kilo : coût de production ou de revient du producteur par kilo en FCFA (nombre), sinon null
N'inventez JAMAIS une valeur absente : mettez null."""
CLES = ("produit", "quantite_kg", "lieu", "prix_propose_kilo", "cout_revient_kilo")


def extraire_llm(texte):
    """Dict des CLES, ou None si le LLM est indisponible (le calcul retombe alors sur les règles locales)."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    corps = {"systemInstruction": {"parts": [{"text": CONSIGNE}]},
             "contents": [{"role": "user", "parts": [{"text": texte}]}],
             "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    for modele in modeles_llm():
        try:
            r = requests.post(GEMINI_URL.format(model=modele), headers={"x-goog-api-key": key},
                              json=corps, timeout=15)
            if r.status_code != 200:
                continue
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            d = json.loads(txt)
            if isinstance(d, dict):
                return {k: d.get(k) for k in CLES}
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------------- voix
def _wav(pcm, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def synthese_wav(texte):
    """Texte -> audio WAV (octets). Lève ServiceIndisponible : le navigateur lit alors la réponse avec sa propre voix."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ServiceIndisponible("GEMINI_API_KEY manquante")
    corps = {"contents": [{"parts": [{"text": "Lisez ce texte en français, d'une voix posée, claire et chaleureuse : " + texte}]}],
             "generationConfig": {"responseModalities": ["AUDIO"],
                                  "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voix_tts()}}}}}
    derniere = "aucun modèle"
    for modele in modeles_tts():
        try:
            r = requests.post(GEMINI_URL.format(model=modele), headers={"x-goog-api-key": key}, json=corps, timeout=40)
            if r.status_code != 200:
                derniere = f"{modele} {r.status_code}"
                continue
            part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
            return _wav(base64.b64decode(part["data"]), int(m.group(1)) if m else 24000)
        except Exception as e:  # réponse inattendue, délai dépassé...
            derniere = f"{modele} {type(e).__name__}"
    raise ServiceIndisponible(derniere)
