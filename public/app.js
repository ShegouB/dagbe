"use strict";
/* Dagbé — interface vocale. Aucune donnée de prix n'est calculée ici : le navigateur n'affiche que ce que le serveur renvoie. */
const $ = (id) => document.getElementById(id);
const app = $("app"), orbe = $("orbe"), lecteur = $("lecteur");
const SILENCE_WAV = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
const params = new URLSearchParams(location.search);

let etat = "repos";            // repos | ecoute | reflexion | parole
let dialogue = null;           // état du dialogue renvoyé par le serveur (question en attente)
let derniereParole = "";
let flux = null, enregistreur = null, morceaux = [], vad = null, vadCtx = null, annule = false;
let audioDebloque = false, urlAudio = null, verrou = null;

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};
const vibre = (p) => { try { navigator.vibrate && navigator.vibrate(p); } catch (e) {} };

// ---------------------------------------------------------------- états de la bulle
const LIBELLES = {
  repos: "Appuyez pour parler", ecoute: "Je vous écoute… parlez", reflexion: "Je réfléchis…", parole: "Je vous réponds…",
};
function setEtat(e, texte) {
  etat = e;
  orbe.dataset.etat = e;
  orbe.setAttribute("aria-label", e === "ecoute" ? "Appuyer pour arrêter" : e === "parole" ? "Appuyer pour interrompre" : "Appuyer pour parler");
  $("statut").textContent = texte || LIBELLES[e];
  if (e !== "ecoute") orbe.style.setProperty("--lvl", 0);
  if (e === "repos") liberer(); else garder();
}
async function garder() { try { if (!verrou && navigator.wakeLock) verrou = await navigator.wakeLock.request("screen"); } catch (e) {} }
function liberer() { try { verrou && verrou.release(); } catch (e) {} verrou = null; }

function debloquerAudio() {   // pendant un geste de l'utilisateur (Safari / iOS)
  if (audioDebloque) return;
  audioDebloque = true;
  lecteur.src = SILENCE_WAV;
  lecteur.play().catch(() => {});
}

// ---------------------------------------------------------------- enregistrement, arrêt automatique au silence
function typeMime() {
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"])
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
  return "";
}

async function ecouter() {
  debloquerAudio();
  if (navigator.onLine === false) return signaler("Pas de connexion. Réessayez quand le réseau revient.");
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    $("form-texte").hidden = false; $("btn-ecrire").hidden = true;
    return signaler("Ce téléphone ne permet pas l'enregistrement. Écrivez votre demande.");
  }
  try {
    flux = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch (e) {
    return signaler("Le micro est bloqué. Autorisez-le dans les réglages du navigateur, ou écrivez votre demande.");
  }
  const mime = typeMime();
  enregistreur = new MediaRecorder(flux, mime ? { mimeType: mime } : undefined);
  morceaux = []; annule = false;
  enregistreur.ondataavailable = (ev) => ev.data.size && morceaux.push(ev.data);
  enregistreur.onstop = surArret;
  enregistreur.start();
  vibre(35);
  setEtat("ecoute");
  detecterSilence(flux);
}

function detecterSilence(stream) {
  const AC = window.AudioContext || window.webkitAudioContext;
  vadCtx = new AC();
  const src = vadCtx.createMediaStreamSource(stream), an = vadCtx.createAnalyser();
  an.fftSize = 1024; src.connect(an);
  const buf = new Uint8Array(an.fftSize);
  const t0 = Date.now(); let bruit = 0, n = 0, parle = false, dernierSon = Date.now();
  vad = setInterval(() => {
    an.getByteTimeDomainData(buf);
    let s = 0; for (const v of buf) { const x = (v - 128) / 128; s += x * x; }
    const rms = Math.sqrt(s / buf.length), dt = Date.now() - t0;
    orbe.style.setProperty("--lvl", Math.min(1, rms * 7).toFixed(2));
    if (dt < 500) { bruit += rms; n++; return; }                     // mesure du bruit ambiant
    const seuil = Math.max(0.025, (bruit / Math.max(n, 1)) * 2.5);
    if (rms > seuil) { parle = true; dernierSon = Date.now(); }
    if ((parle && Date.now() - dernierSon > 1700) || dt > 30000 || (!parle && dt > 9000)) arreter();
  }, 100);
}

function arreter() {
  if (vad) { clearInterval(vad); vad = null; }
  if (vadCtx) { try { vadCtx.close(); } catch (e) {} vadCtx = null; }
  if (enregistreur && enregistreur.state !== "inactive") enregistreur.stop();
}

async function surArret() {
  flux && flux.getTracks().forEach((t) => t.stop());
  if (annule) return;
  vibre([20, 40, 20]);
  const blob = new Blob(morceaux, { type: enregistreur.mimeType || "audio/webm" });
  const ext = /mp4/.test(blob.type) ? "mp4" : /ogg/.test(blob.type) ? "ogg" : "webm";
  const fd = new FormData();
  fd.append("audio", blob, "audio." + ext);
  await demander(fd);
}

// ---------------------------------------------------------------- dialogue avec le serveur
async function demander(fd) {
  setEtat("reflexion");
  if (dialogue) fd.append("state", JSON.stringify(dialogue));
  let r;
  try {
    const rep = await fetch("/api/advice", { method: "POST", body: fd });
    if (!rep.ok) throw new Error("http " + rep.status);
    r = await rep.json();
  } catch (e) {
    return signaler("Je n'arrive pas à joindre le serveur. Vérifiez votre connexion et réessayez.", true);
  }
  dialogue = r.state || null;
  afficher(r);
  await dire(r.reponse_vocale || r.reponse_texte, r.status === "needs_info");
}

function signaler(msg, parler) {
  setEtat("repos", msg);
  if (parler) dire(msg, false);
}

// ---------------------------------------------------------------- voix : Gemini (gratuit) puis voix du téléphone
async function dire(texte, reecouterApres) {
  derniereParole = texte;
  const bt = $("btn-reecouter"); if (bt) bt.disabled = false;
  setEtat("parole", reecouterApres ? "Je vous pose une question…" : "Je vous réponds…");
  let fini = false;
  const suite = () => { if (fini) return; fini = true; apresParole(reecouterApres); };
  if (store.get("dagbe-eco") === "1") return voixTelephone(texte, suite);   // mode économie de données
  try {
    const fd = new FormData(); fd.append("text", texte);
    const rep = await fetch("/api/tts", { method: "POST", body: fd });
    if (!rep.ok) throw new Error("tts " + rep.status);
    const blob = await rep.blob();
    if (urlAudio) URL.revokeObjectURL(urlAudio);
    urlAudio = URL.createObjectURL(blob);
    lecteur.src = urlAudio;
    lecteur.onended = suite;
    lecteur.onerror = () => { lecteur.onended = null; voixTelephone(texte, suite); };
    await lecteur.play();
  } catch (e) {
    lecteur.onended = null;
    voixTelephone(texte, suite);
  }
}

function voixTelephone(texte, quandFini) {
  if (!("speechSynthesis" in window)) return quandFini();
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(texte);
  u.lang = "fr-FR"; u.rate = 0.95;
  const v = speechSynthesis.getVoices().find((x) => /^fr/i.test(x.lang));
  if (v) u.voice = v;
  u.onend = quandFini; u.onerror = quandFini;
  speechSynthesis.speak(u);
}

function stopParole() {
  lecteur.onended = null; lecteur.pause();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

function apresParole(reecouter) {
  if (reecouter) { setEtat("repos", "À vous…"); ecouter(); }          // question posée : le micro se rouvre tout seul
  else setEtat("repos", "Appuyez pour une nouvelle demande");
}

// ---------------------------------------------------------------- affichage
function el(tag, cls, txt) { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
const F = (n) => Math.round(Math.abs(n)).toLocaleString("fr-FR").replace(/ /g, " ");
const signe = (n) => (n < 0 ? "−" : "+") + F(n);
const ICONES = {
  ok: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  non: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  attention: '<svg viewBox="0 0 24 24"><path d="M12 5v9M12 19v.01"/></svg>',
  balance: '<svg viewBox="0 0 24 24"><path d="M5 19V9M12 19V5M19 19v-7"/></svg>',
  son: '<svg viewBox="0 0 24 24"><path d="M4 9.5v5h4l5 4v-13l-5 4zM16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/></svg>',
  partage: '<svg viewBox="0 0 24 24"><path d="M12 15V3M8 7l4-4 4 4M6 11H5v10h14V11h-1"/></svg>',
  menu: '<svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/></svg>',
};
function icone(nom) { const s = el("span"); s.innerHTML = ICONES[nom]; return s.firstChild; }   // constantes internes, jamais de texte utilisateur

function puce(libelle, valeur, cls) { const p = el("div", "puce " + (cls || "")); p.append(el("small", "", libelle), el("b", "", valeur)); return p; }

function afficher(r, demo) {
  const f = $("feuille"); f.replaceChildren();
  app.dataset.vue = "resultat";
  if (demo) f.append(el("div", "demo-tag", "EXEMPLE ILLUSTRATIF — chiffres fictifs"));
  if (r.transcription) f.append(el("p", "cite", "« " + r.transcription + " »"));

  if (r.status === "needs_info") {
    const d = r.donnees || {};
    const c = el("div", "cle q"); c.append(el("small", "", "Dagbé vous demande"), el("strong", "", (r.questions && r.questions[0]) || r.reponse_texte));
    f.append(c);
    const pc = el("div", "puces");
    pc.append(puce("Produit", r.produit_fr || "à préciser", r.produit_fr ? "ok" : "vide"),
              puce("Marché", d.lieu || "à préciser", d.lieu ? "ok" : "vide"),
              puce("Quantité", d.quantite_kg ? F(d.quantite_kg) + " kg" : "à préciser", d.quantite_kg ? "ok" : "vide"));
    f.append(pc);
    if (r.notes && r.notes.length) f.append(el("p", "cite", r.notes.join(" ")));
    return;
  }
  if (r.status === "error") {
    const c = el("div", "cle q"); c.append(el("small", "", "Un instant"), el("strong", "", r.reponse_texte));
    f.append(c);
    return;
  }

  // verdict (feu tricolore)
  const v = r.verdict_offre;
  let cls = "neutre", ico = "balance", titre = "Comparaison des marchés", sous = [r.produit_fr, r.donnees && r.donnees.lieu].filter(Boolean).join(" · ");
  if (v) {
    const e = String(Math.abs(v.ecart_pct)).replace(".", ",");
    if (v.niveau === "inférieure") {
      cls = "rouge"; ico = "non"; titre = "Offre trop basse";
      sous = e + " % sous le prix du marché" + (v.sous_minimum_12_mois ? ", sous le minimum de l'année" : "");
    } else if (v.niveau === "supérieure") {
      cls = "vert"; ico = "ok"; titre = "Bonne offre"; sous = e + " % au-dessus du prix du marché";
    } else {
      cls = "ambre"; ico = "attention"; titre = "Offre correcte"; sous = "Proche du prix du marché (" + (v.ecart_pct >= 0 ? "+" : "−") + e + " %)";
    }
  }
  const ver = el("div", "verdict " + cls), pa = el("div", "pastille");
  pa.append(icone(ico));
  const tx = el("div"); tx.append(el("h2", "", titre), el("p", "", sous));
  ver.append(pa, tx); f.append(ver);

  // chiffre clé
  const rs = r.resume;
  if (rs) {
    const c = el("div", "cle");
    if (rs.ailleurs) {
      c.append(el("small", "", "Meilleur net : " + rs.meilleure), el("strong", "", "+" + F(rs.gain_fcfa) + " F"),
               el("span", "", "de plus que " + (rs.base_est_offre ? "votre offre" : "la vente sur place") + ", transport déduit (" + rs.distance_km + " km)"));
    } else {
      c.append(el("small", "", "Meilleure option"), el("strong", "", "Vendre sur place"),
               el("span", "", "Après transport, c'est le plus avantageux parmi les marchés comparés"));
    }
    f.append(c);
  }

  // puces
  const pc = el("div", "puces");
  if (r.reference) pc.append(puce("Prix du marché", F(r.reference.prix) + "\u00a0F/kg"));
  const offre = r.donnees && r.donnees.prix_propose_kilo;
  if (offre) pc.append(puce("Votre offre", F(offre) + "\u00a0F/kg"));
  if (rs && rs.marge_offre_fcfa != null) pc.append(puce("Votre marge", signe(rs.marge_offre_fcfa) + "\u00a0F", rs.marge_offre_fcfa < 0 ? "moins" : "plus"));
  if (pc.children.length) f.append(pc);

  // détail
  const det = el("details", "detail"); det.append(el("summary", "", "Détail, hypothèses et sources"));
  (r.options || []).forEach((o, i) => {
    const row = el("div", "opt" + (i === 0 && r.options.length > 1 && rs && rs.ailleurs ? " top" : ""));
    const n = el("div", "n"); n.append(el("b", "", o.marche), el("small", "", o.distance_km ? o.distance_km + " km · transport " + F(o.transport_fcfa) + " F" : "Sur place"));
    const val = el("div", "v"); val.append(el("b", "", F(o.net_total_fcfa) + " F"), el("small", "", F(o.prix_kg) + " F/kg"));
    row.append(n, val); det.append(row);
  });
  const notes = el("div", "notes");
  for (const l of (r.reponse_texte || "").split("\n")) if (/^(Hypothèses|Attention)/.test(l)) notes.append(el("p", "", l));
  if (r.reference) notes.append(el("p", "", "Source : WFP via HDX HAPI · " + r.reference.marche + " · relevé du " + new Date(r.reference.date).toLocaleDateString("fr-FR") + ". Net = revenu − transport estimé."));
  det.append(notes); f.append(det);

  // actions
  const act = el("div", "actions");
  const b1 = el("button", "btn-clair"); b1.id = "btn-reecouter"; b1.type = "button"; b1.append(icone("son"), document.createTextNode("Réécouter"));
  b1.addEventListener("click", () => { if (derniereParole && etat === "repos") dire(derniereParole, false); });
  const b2 = el("button", "btn-clair", "Nouvelle demande"); b2.type = "button"; b2.addEventListener("click", nouvelle);
  act.append(b1, b2); f.append(act);
  f.scrollTop = 0;
}

function nouvelle() {
  stopParole(); annule = true; arreter(); dialogue = null;
  $("feuille").replaceChildren(); app.dataset.vue = "accueil";
  setEtat("repos");
}

// ---------------------------------------------------------------- exemple (aucun appel serveur, aucune donnée réelle)
const EXEMPLE = {
  status: "ok", transcription: "On m'offre 150 francs le kilo de maïs à Allada, j'ai mille kilos. Ça me coûte 110 francs le kilo.",
  produit_fr: "maïs blanc", donnees: { lieu: "Allada", quantite_kg: 1000, prix_propose_kilo: 150, cout_revient_kilo: 110 },
  verdict_offre: { niveau: "inférieure", ecart_pct: -16.7, sous_minimum_12_mois: false },
  reference: { prix: 180, marche: "Marché exemple", date: "2026-10-31" },
  resume: { meilleure: "Cotonou", distance_km: 45, ailleurs: true, gain_fcfa: 23250, base_est_offre: true, marge_offre_fcfa: 40000 },
  options: [
    { marche: "Cotonou", prix_kg: 180, distance_km: 45, transport_fcfa: 6750, net_total_fcfa: 173250, est_offre_recue: false },
    { marche: "Allada (offre reçue)", prix_kg: 150, distance_km: 0, transport_fcfa: 0, net_total_fcfa: 150000, est_offre_recue: true },
  ],
  reponse_texte: "Hypothèses : transport ≈ 0,15 F par kg et par km (distance routière estimée). La décision de vendre vous appartient.",
  reponse_vocale: "Exemple illustratif, avec des chiffres fictifs. Pour mille kilos de maïs à Allada, le prix de référence est de cent quatre-vingts francs le kilo. Votre offre est inférieure de seize virgule sept pour cent. Avec votre coût, elle vous laisse quarante mille francs de marge. Le meilleur net est à Cotonou, vingt-trois mille deux cent cinquante francs de plus, transport déduit. La décision de vendre vous appartient.",
};
const EXEMPLE_QUESTION = {
  status: "needs_info", transcription: "On me propose 120 francs le kilo de maïs.", produit_fr: "maïs blanc",
  donnees: { lieu: null, quantite_kg: null }, questions: ["Dans quel marché ou quelle commune se trouve l'offre ?"],
  reponse_vocale: "Dans quel marché ou quelle commune se trouve l'offre ?", reponse_texte: "Dans quel marché ou quelle commune se trouve l'offre ?",
};
function montrerExemple(parler) {
  stopParole(); dialogue = null; afficher(EXEMPLE, true);
  derniereParole = EXEMPLE.reponse_vocale;
  if (parler) { setEtat("parole", "Voici un exemple de réponse"); voixTelephone(EXEMPLE.reponse_vocale, () => setEtat("repos", "Appuyez pour parler")); }
}

// ---------------------------------------------------------------- réglages
const reglages = $("reglages"), voile = $("voile");
function ouvrirReglages() { reglages.hidden = false; voile.hidden = false; $("btn-fermer").focus(); }
function fermerReglages() { reglages.hidden = true; voile.hidden = true; $("btn-menu").focus(); }
$("btn-menu").addEventListener("click", ouvrirReglages);
$("btn-fermer").addEventListener("click", fermerReglages);
voile.addEventListener("click", fermerReglages);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !reglages.hidden) fermerReglages(); });
$("opt-eco").checked = store.get("dagbe-eco") === "1";
$("opt-eco").addEventListener("change", (e) => store.set("dagbe-eco", e.target.checked ? "1" : "0"));
$("btn-exemple").addEventListener("click", () => { fermerReglages(); debloquerAudio(); montrerExemple(true); });

// ---------------------------------------------------------------- commandes
orbe.addEventListener("click", () => {
  if (etat === "ecoute") return arreter();
  if (etat === "parole") { stopParole(); return setEtat("repos", "Appuyez pour parler"); }
  if (etat === "repos") ecouter();
});
function ouvrirSaisie() { $("form-texte").hidden = false; $("btn-ecrire").hidden = true; $("saisie").focus(); }
$("btn-ecrire").addEventListener("click", ouvrirSaisie);
$("btn-ecrire2").addEventListener("click", () => { fermerReglages(); ouvrirSaisie(); });
$("form-texte").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const t = $("saisie").value.trim(); if (!t || etat === "reflexion") return;
  debloquerAudio(); stopParole(); const fd = new FormData(); fd.append("text", t); $("saisie").value = ""; demander(fd);
});

// ---------------------------------------------------------------- installation (PWA)
const UA = navigator.userAgent;
const dejaInstallee = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const sim = params.get("sim");
const ios = sim === "ios" || /iphone|ipad|ipod/i.test(UA) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const android = sim === "android" || /android/i.test(UA);
const navIntegre = sim === "inapp" || /FBAN|FBAV|FB_IAB|Instagram|WhatsApp|Line\/|MicroMessenger|Snapchat|TikTok|; wv\)/i.test(UA);
let invite = null, bandeauMontre = false;

function etapes(liste) {
  const ol = $("install-etapes"); ol.replaceChildren();
  liste.forEach((t) => { const li = el("li"); li.innerHTML = t; ol.append(li); });   // textes fixes ci-dessous
  ol.hidden = !liste.length;
}
function montrerBandeau(force) {
  if (dejaInstallee) return;
  if (!force && Number(store.get("dagbe-install-non") || 0) > Date.now() - 3 * 864e5) return;
  const b = $("install"), go = $("install-go"), titre = $("install-titre"), aide = $("install-aide");
  go.hidden = false; go.onclick = null; bandeauMontre = true;
  if (navIntegre) {
    titre.textContent = "Ouvrez Dagbé dans Chrome pour l'installer";
    aide.textContent = "Ce lien s'est ouvert dans une application (WhatsApp, Facebook…) qui ne permet pas l'installation.";
    etapes([`Touchez <b>${ICONES.menu}</b> en haut à droite`, "Choisissez « Ouvrir dans Chrome » ou « Ouvrir dans le navigateur »"]);
    go.textContent = "Copier le lien";
    go.onclick = async () => { try { await navigator.clipboard.writeText(location.origin); go.textContent = "Lien copié ✓"; } catch (e) { go.textContent = location.origin; } };
  } else if (invite && !sim) {
    titre.textContent = "Installez Dagbé sur votre téléphone";
    aide.textContent = "Accès direct depuis votre écran d'accueil, en plein écran.";
    etapes([]);
    go.textContent = "Installer";
    go.onclick = async () => { b.hidden = true; invite.prompt(); try { await invite.userChoice; } catch (e) {} invite = null; };
  } else if (ios) {
    titre.textContent = "Ajoutez Dagbé à votre écran d'accueil";
    aide.textContent = "Sur iPhone, cela se fait depuis Safari en 3 gestes :";
    etapes([`Touchez <b>${ICONES.partage}</b> Partager (en bas de l'écran)`, "Choisissez « Sur l'écran d'accueil »", "Touchez « Ajouter »"]);
    go.textContent = "J'ai compris"; go.onclick = () => $("install-non").click();
  } else if (android) {
    titre.textContent = "Installez Dagbé sur votre téléphone";
    aide.textContent = "Depuis Chrome :";
    etapes([`Touchez <b>${ICONES.menu}</b> en haut à droite`, "Choisissez « Installer l'application » (ou « Ajouter à l'écran d'accueil »)"]);
    go.textContent = "J'ai compris"; go.onclick = () => $("install-non").click();
  } else { return; }
  b.hidden = false;
  $("btn-installer").hidden = false;
}
$("install-non").addEventListener("click", () => { $("install").hidden = true; store.set("dagbe-install-non", String(Date.now())); });
$("btn-installer").addEventListener("click", () => { fermerReglages(); montrerBandeau(true); window.scrollTo(0, 0); });
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); invite = e; montrerBandeau(); });
window.addEventListener("appinstalled", () => { $("install").hidden = true; $("btn-installer").hidden = true; invite = null; });
if (!dejaInstallee) {
  setTimeout(() => { if (!bandeauMontre && (ios || navIntegre || sim)) montrerBandeau(!!sim); }, 900);                // iPhone / navigateur intégré : pas d'invite native
  setTimeout(() => { if (!bandeauMontre && android) montrerBandeau(); }, 3500);                                      // Android : mode d'emploi si Chrome n'a pas encore proposé
}

// ---------------------------------------------------------------- démarrage
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
window.addEventListener("offline", () => setEtat(etat, "Pas de connexion"));
window.addEventListener("online", () => etat === "repos" && setEtat("repos"));

const ex = params.get("exemple");                       // ?exemple (aperçu) ou ?exemple=question
if (ex !== null) { if (ex === "question") afficher(EXEMPLE_QUESTION); else montrerExemple(false); setEtat("repos"); }

fetch("/api/health").then((r) => r.json()).then((h) => {
  if (h.base && h.base.ok && h.asr && h.llm) return;
  const manque = [];
  if (!h.base || !h.base.ok) manque.push("base de prix");
  if (!h.asr) manque.push("clé Groq");
  if (!h.llm) manque.push("clé Gemini");
  $("statut").textContent = "Configuration incomplète : " + manque.join(", ");
}).catch(() => {});
