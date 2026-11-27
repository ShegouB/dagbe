# Dagbé — le bon prix, à voix haute

Un micro, une question en français, une réponse **parlée** : l'agriculteur dit l'offre qu'on lui fait,
l'application compare avec les prix du registre public et dit si l'offre est bonne, avec la marge et les
marchés accessibles (transport déduit).

```
micro (navigateur) ─audio→ /api/advice ─→ Whisper (Groq) ─→ Gemini (extraction JSON)
                                       ─→ calcul déterministe sur data/prices.db ─→ texte + phrase à dire
navigateur ─texte→ /api/tts ─→ Gemini TTS (gratuit)  ──(sinon)──→ voix du navigateur
```

Règle d'or : le modèle comprend, il ne calcule pas. Prix, distances, marges viennent de `data/prices.db` et
de `agriprix/core.py`. Information manquante → le serveur pose la question **à voix haute** et le micro se rouvre.
Produit ou lieu inconnu → il le dit au lieu d'inventer.

## 0. Application mobile installable (PWA)
Dagbé est pensé pour le téléphone : bulle vocale au pouce, résultat en feuille avec feu tricolore (vert / orange / rouge)
et un seul chiffre clé, détails repliés. Dès l'ouverture sur le site déployé, un bandeau propose l'installation :
- **Android / Chrome** : bouton « Installer » (invite native captée) ; sinon mode d'emploi en 2 étapes.
- **iPhone / Safari** : pas d'invite native possible, le bandeau explique « Partager → Sur l'écran d'accueil ».
- **Lien ouvert depuis WhatsApp / Facebook** : ces navigateurs intégrés ne peuvent pas installer ; le bandeau demande
  d'ouvrir dans Chrome et propose « Copier le lien ».
L'application s'ouvre ensuite en plein écran et reste lisible hors connexion (écran d'accueil + exemple). Menu ☰ :
« Économiser mes données » (voix du téléphone au lieu de la voix Gemini, ~0,5–1 Mo par réponse), exemple, saisie au clavier.
`/qr.html` affiche un QR code à faire scanner pendant la démo. Aperçus sans serveur : `/?exemple`, `/?exemple=question`,
`/?sim=ios|android|inapp`.

## Prix collectés auprès des utilisateurs (nouveau)
Onglets **Parler** (conseil sur une offre), **Prix** (choisir une zone et voir les prix, avec source, date, fiabilité et marchés
voisins) et **Contribuer** (donner un prix au formulaire ou à la voix ; la voix ne fait que pré-remplir, l'utilisateur confirme).

Croisement avec HDX (`agriprix/core.py`, fonction `resoudre`) :
- contributions des **14 derniers jours**, médiane par marché et produit, sans mélanger détail et gros ;
- **≥ 3 contributions** : le prix collecté fait référence. Fiabilité « Fiable » s'il est à ±25 % du dernier prix HDX, sinon « Indicatif ».
  Moins de 3 : HDX reste la référence et la collecte est mentionnée ; sans HDX : « Peu de données » ;
- une contribution à moins de 0,4× ou plus de 2,5× le dernier prix HDX est enregistrée « à vérifier » et **n'est pas utilisée** ;
- limites : 10 contributions par appareil et 30 par adresse réseau et par 24 h ; prix entre 10 et 50 000 F/kg.

**Si HDX ou la base des contributions est indisponible**, Dagbé répond quand même : copie HDX embarquée (`data/prices.db`) et/ou
contributions ; si aucune source n'a de prix, il le dit sans rien inventer.

**Activer la collecte** (Vercel ne peut pas écrire sur son disque) : *Vercel → projet → Storage → Create Database → Neon (Postgres)*
puis le connecter au projet : la variable `DATABASE_URL` est ajoutée automatiquement ; *Redeploy*. La table se crée toute seule.
`/api/health` indique `collecte.ok`. Modération dans la console SQL de Neon :
```sql
SELECT id, product, zone, price, created_at FROM contributions WHERE status = 'a_verifier' ORDER BY id DESC;
UPDATE contributions SET status = 'ok' WHERE id = 123;       -- confirmer
DELETE FROM contributions WHERE id = 123;                    -- supprimer
```
Sans `DATABASE_URL`, tout le reste fonctionne ; seul l'envoi d'une contribution répond « collecte pas encore activée ».
Pour tester en local sans Postgres : `DATABASE_URL=sqlite:///contrib.db`.

## 1. Préparer les données (une seule fois, sur votre machine)

```bash
python scripts/build_data.py ~/Downloads/hdx_hapi_food_price_ben.csv
```
Crée `data/prices.db` (prix au kilo, 15 derniers mois, quelques Mo). **Ce fichier doit être versionné** : c'est lui
que Vercel déploie. Le script affiche les produits, zones et le nombre de zones avec coordonnées.

## 2. Lancer en local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # puis remplir GROQ_API_KEY et GEMINI_API_KEY
uvicorn app:app --env-file .env --reload
```
Ouvrir http://localhost:8000 (ou l'adresse HTTPS de Vercel sur téléphone) (le micro fonctionne sur `localhost` et en HTTPS). Tests : `python -m pytest -q`.

## 3. Déployer sur Vercel (gratuit, offre Hobby)

Vercel exécute FastAPI nativement ; `app.py` expose `app`, `public/` est servi en statique.
1. Mettre le dossier sur GitHub (avec `data/prices.db`, **sans** `.env`).
2. vercel.com → *Add New… → Project* → importer le dépôt (preset FastAPI détecté tout seul).
3. *Settings → Environment Variables* : `GROQ_API_KEY`, `GEMINI_API_KEY` → *Deploy*.
   (ou en ligne de commande : `npm i -g vercel`, `vercel`, puis `vercel --prod`).
4. L'adresse `https://….vercel.app` est en HTTPS : le micro marche, y compris sur téléphone.
5. Vérifier `https://…/api/health` : `base.ok`, `asr`, `llm` doivent être vrais.

Limites Vercel à connaître : corps de requête ≤ 4,5 Mo (un enregistrement de 30 s fait ~0,3 Mo, c'est large) ;
aucune mémoire entre deux requêtes (c'est pourquoi l'état du dialogue voyage dans le navigateur).

## 4. La voix gratuite (Gemini TTS)
La même clé Gemini sert à la voix : créez-la sur https://aistudio.google.com/apikey (palier gratuit, quotas limités,
modèles encore en « preview »). Aucune configuration de plus. Si le quota est dépassé ou le modèle indisponible, le
navigateur lit la réponse avec sa propre voix française : la démo ne tombe jamais en silence.
Réglages optionnels (variables d'environnement) : `TTS_MODELS`, `TTS_VOICE` (ex. `Kore`, `Puck`), `GEMINI_MODEL`.

## 5. Hypothèses à valider avec l'équipe
- Transport : 0,15 F/kg/km × distance routière (1,3 × vol d'oiseau), marchés à ≤ 150 km (`TRANSPORT_FCFA_PAR_KG_KM`, `MAX_DISTANCE_KM`).
- Prix de référence : dernier prix **de détail** observé dans la zone ; verdict ±10 %. Un producteur vend souvent en gros : à trancher (les contributions distinguent détail et gros).
- Un prix de plus de 90 jours (par rapport au plus récent du produit) n'est pas proposé comme alternative.
- Produits reconnus : synonymes français dans `agriprix/core.py` (`SYNONYMS`) ; à compléter avec la liste réelle.

Sécurité : ne jamais mettre les clés dans le code ni sur GitHub ; révoquer toute clé déjà exposée.

## Noms de produits en français

Les noms HDX (anglais) sont traduits par `core.label()`. Pour lister ceux qui ne le sont pas encore
(avec `data/prices.db` présent) :

```bash
python -c "from agriprix import core; print(core.non_traduits())"
```

Ajoutez les entrées manquantes dans `BASE_FR` / `QUAL_FR` de `agriprix/core.py`.
