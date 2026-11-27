"use strict";
/* Dagbé — interface. Le navigateur n'affiche que ce que le serveur renvoie : aucun prix n'est calculé ici. */
const $ = (id) => document.getElementById(id);
const app = $("app"), lecteur = $("lecteur");
const SILENCE_WAV = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
const params = new URLSearchParams(location.search);

let etat = "repos";            // repos | ecoute | reflexion | parole
let cible = "parler";          // qui utilise le micro : « parler » (conseil) ou « contrib » (formulaire)
let dialogue = null;           // état du dialogue renvoyé par le serveur
let derniereParole = "";
let flux = null, enregistreur = null, morceaux = [], vad = null, vadCtx = null, annule = false;
let audioDebloque = false, urlAudio = null, verrou = null;

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};
const vibre = (p) => { try { navigator.vibrate && navigator.vibrate(p); } catch (e) {} };
function el(tag, cls, txt) { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
const F = (n) => Math.round(Math.abs(n)).toLocaleString("fr-FR").replace(/ /g, " ");
const signe = (n) => (n < 0 ? "−" : "+") + F(n);
const mois = (d) => new Date(d).toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
const jour = (d) => new Date(d).toLocaleDateString("fr-FR", { day: "numeric", month: "long" });

const ICONES = {
  partage: '<svg viewBox="0 0 24 24"><path d="M12 15V3M8 7l4-4 4 4M6 11H5v10h14V11h-1"/></svg>',
  menu: '<svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/></svg>',
  son: '<svg viewBox="0 0 24 24"><path d="M4 9.5v5h4l5 4v-13l-5 4zM16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/></svg>',
};
function icone(nom) { const s = el("span"); s.innerHTML = ICONES[nom]; return s.firstChild; }   // constantes internes uniquement

// ---------------------------------------------------------------- onglets
const VUES = { parler: $("v-parler"), prix: $("v-prix"), contribuer: $("v-contribuer") };
function allerA(nom) {
  if (etat !== "repos" || nom !== "parler") { stopTout(); }
  app.dataset.onglet = nom;
  for (const k in VUES) VUES[k].hidden = k !== nom;
  document.querySelectorAll(".onglets button").forEach((b) => b.setAttribute("aria-current", b.dataset.onglet === nom ? "page" : "false"));
  if (nom !== "parler") $("install").hidden = true;
  if (nom === "prix" || nom === "contribuer") chargerListes();
  if (nom === "contribuer" && zoneChoisie && $("c-zone").options.length > 1 && !$("c-zone").dataset.touche) $("c-zone").value = zoneChoisie;
  store.set("dagbe-onglet", nom);
}
document.querySelectorAll(".onglets button").forEach((b) => b.addEventListener("click", () => allerA(b.dataset.onglet)));
function stopTout() {
  stopParole(); annule = true; arreter(); setEtat("repos", undefined, true);
}

// ---------------------------------------------------------------- état du micro
const LIBELLES = { repos: "Appuyez pour parler", ecoute: "Je vous écoute… parlez", reflexion: "Je réfléchis…", parole: "Je vous réponds…" };
const LIBELLES_CONTRIB = { repos: "Ex. : « Le maïs est à 180 francs au marché »", ecoute: "Je vous écoute…", reflexion: "Je cherche le produit et le prix…", parole: "" };
function ui() { return cible === "contrib" ? { bouton: $("mic-contrib"), statut: $("contrib-statut"), lib: LIBELLES_CONTRIB } : { bouton: $("orbe"), statut: $("statut"), lib: LIBELLES }; }
function setEtat(e, texte, tous) {
  etat = e; app.dataset.etat = e;
  for (const c of tous ? ["parler", "contrib"] : [cible]) {
    const sauve = cible; cible = c;
    const u = ui();
    u.bouton.dataset.etat = e;
    u.bouton.setAttribute("aria-label", e === "ecoute" ? "Appuyer pour arrêter" : e === "parole" ? "Appuyer pour interrompre" : c === "contrib" ? "Dire le prix à voix haute" : "Appuyer pour parler");
    u.statut.textContent = texte || u.lib[e];
    if (e !== "ecoute") u.bouton.style.setProperty("--lvl", 0);
    cible = sauve;
  }
  if (e === "repos") liberer(); else garder();
}
async function garder() { try { if (!verrou && navigator.wakeLock) verrou = await navigator.wakeLock.request("screen"); } catch (e) {} }
function liberer() { try { verrou && verrou.release(); } catch (e) {} verrou = null; }
function debloquerAudio() {
  if (audioDebloque) return;
  audioDebloque = true; lecteur.src = SILENCE_WAV; lecteur.play().catch(() => {});
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
    if (cible === "parler") { $("form-texte").hidden = false; $("btn-ecrire").hidden = true; }
    return signaler("Ce téléphone ne permet pas l'enregistrement. Écrivez votre demande.");
  }
  try {
    flux = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch (e) {
    return signaler("Le micro est bloqué. Autorisez-le dans les réglages du navigateur, ou écrivez.");
  }
  const mime = typeMime();
  enregistreur = new MediaRecorder(flux, mime ? { mimeType: mime } : undefined);
  morceaux = []; annule = false;
  enregistreur.ondataavailable = (ev) => ev.data.size && morceaux.push(ev.data);
  enregistreur.onstop = surArret;
  enregistreur.start();
  vibre(35); setEtat("ecoute"); detecterSilence(flux);
}
function detecterSilence(stream) {
  const AC = window.AudioContext || window.webkitAudioContext;
  vadCtx = new AC();
  const src = vadCtx.createMediaStreamSource(stream), an = vadCtx.createAnalyser();
  an.fftSize = 1024; src.connect(an);
  const buf = new Uint8Array(an.fftSize), bouton = ui().bouton;
  const t0 = Date.now(); let bruit = 0, n = 0, parle = false, dernierSon = Date.now();
  vad = setInterval(() => {
    an.getByteTimeDomainData(buf);
    let s = 0; for (const v of buf) { const x = (v - 128) / 128; s += x * x; }
    const rms = Math.sqrt(s / buf.length), dt = Date.now() - t0;
    bouton.style.setProperty("--lvl", Math.min(1, rms * 7).toFixed(2));
    if (dt < 500) { bruit += rms; n++; return; }
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
  const fd = new FormData(); fd.append("audio", blob, "audio." + ext);
  if (cible === "contrib") await comprendreContribution(fd); else await demander(fd);
}
function signaler(msg, parler) {
  setEtat("repos", msg);
  if (parler && cible === "parler") dire(msg, false);
}

// ---------------------------------------------------------------- onglet Parler : dialogue avec le serveur
async function demander(fd) {
  setEtat("reflexion");
  if (dialogue) fd.append("state", JSON.stringify(dialogue));
  let r, code = "réseau";
  try {
    const rep = await fetch("/api/advice", { method: "POST", body: fd });
    code = String(rep.status);
    if (!rep.ok) throw new Error("http " + rep.status);
    r = await rep.json();
  } catch (e) {
    console.error("Dagbé /api/advice :", code, e);
    return signaler("Je n'arrive pas à joindre le serveur (erreur " + code + "). Vérifiez votre connexion et réessayez.", true);
  }
  dialogue = r.state || null;
  afficher(r);
  await dire(r.reponse_vocale || r.reponse_texte, r.status === "needs_info");
}

// voix : Gemini (gratuit) puis voix du téléphone
async function dire(texte, reecouterApres) {
  derniereParole = texte;
  const bt = $("btn-reecouter"); if (bt) bt.disabled = false;
  setEtat("parole", reecouterApres ? "Je vous pose une question…" : "Je vous réponds…");
  let fini = false;
  const suite = () => { if (fini) return; fini = true; apresParole(reecouterApres); };
  if (store.get("dagbe-eco") === "1") return voixTelephone(texte, suite);
  try {
    const fd = new FormData(); fd.append("text", texte);
    const rep = await fetch("/api/tts", { method: "POST", body: fd });
    if (!rep.ok) throw new Error("tts " + rep.status);
    const blob = await rep.blob();
    if (urlAudio) URL.revokeObjectURL(urlAudio);
    urlAudio = URL.createObjectURL(blob);
    lecteur.src = urlAudio; lecteur.onended = suite;
    lecteur.onerror = () => { lecteur.onended = null; voixTelephone(texte, suite); };
    await lecteur.play();
  } catch (e) { lecteur.onended = null; voixTelephone(texte, suite); }
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
  if (reecouter) { setEtat("repos", "À vous…"); ecouter(); }
  else setEtat("repos", "Appuyez pour une nouvelle demande");
}


const cap = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
// ---------------------------------------------------------------- pictogrammes de produits (dessinés pour Dagbé)
const sv = (x) => '<svg viewBox="0 0 64 64" aria-hidden="true">' + x + '</svg>';
const bol = (b, m, p) => sv(`<path d="M12 34c2-15 10-21 20-21s18 6 20 21z" fill="${m}"/><g fill="${p}"><circle cx="24" cy="28" r="1.6"/><circle cx="32" cy="22" r="1.6"/><circle cx="40" cy="28" r="1.6"/><circle cx="32" cy="30" r="1.6"/><circle cx="27" cy="22" r="1.4"/><circle cx="37" cy="22" r="1.4"/></g><path d="M8 34h48c0 13-10 22-24 22S8 47 8 34z" fill="${b}"/>`);
const haricot = (c, oeil) => sv(`<ellipse cx="24" cy="30" rx="17" ry="10" transform="rotate(-28 24 30)" fill="${c}"/><ellipse cx="40" cy="38" rx="17" ry="10" transform="rotate(24 40 38)" fill="${c}"/><ellipse cx="18" cy="25" rx="6" ry="2.2" transform="rotate(-28 18 25)" fill="#fff" opacity=".35"/><ellipse cx="45" cy="42" rx="6" ry="2.2" transform="rotate(24 45 42)" fill="#fff" opacity=".35"/>${oeil ? '<circle cx="28" cy="33" r="2.6" fill="#2b1d12"/><circle cx="36" cy="35" r="2.6" fill="#2b1d12"/>' : ''}`);
const tubercule = (c, l) => sv(`<path d="M16 22c8-9 28-6 32 7 3 11-6 25-19 26-13 1-22-9-18-21 1-5 2-9 5-12z" fill="${c}"/><g fill="none" stroke="${l}" stroke-width="2" stroke-linecap="round"><path d="M22 30q8 4 18 0"/><path d="M20 40q10 5 24 0"/><path d="M27 49q7 2 14-1"/></g><path d="M44 17c3-5 8-7 12-6-1 5-5 8-12 6z" fill="#2f6b3a"/>`);
const PICTOS = {
  "maïs": sv('<path d="M32 60C14 52 10 34 19 22c2 14 8 26 13 38z" fill="#2f6b3a"/><path d="M32 60C50 52 54 34 45 22c-2 14-8 26-13 38z" fill="#2f6b3a"/><ellipse cx="32" cy="28" rx="11" ry="23" fill="#f2b632"/><g stroke="#c98a1b" stroke-width="1.6" fill="none" stroke-linecap="round"><path d="M32 6v44M25 12v32M39 12v32M21 22h22M21 32h22M22 42h20"/></g>'),
  "haricot": haricot("#9c3d2a", false), "niébé": haricot("#efe0bd", true),
  "gari": bol("#b5502b", "#f0dca6", "#d2b66c"), "riz": bol("#2f6b3a", "#ffffff", "#d9d3c2"),
  "igname": tubercule("#8a5a35", "#664025"), "manioc": tubercule("#c79a63", "#9a6f3d"),
  "tomate": sv('<circle cx="32" cy="36" r="21" fill="#d2402e"/><ellipse cx="24" cy="27" rx="5" ry="3" transform="rotate(-30 24 27)" fill="#fff" opacity=".3"/><path d="M32 17l5 5 7-2-3 6 5 4-7 1-2 6-5-5-5 5-2-6-7-1 5-4-3-6 7 2z" fill="#2f6b3a"/>'),
  "oignon": sv('<path d="M32 12c3 7 17 12 17 26 0 11-8 17-17 17S15 49 15 38c0-14 14-19 17-26z" fill="#b2557a"/><path d="M32 14c-6 10-9 22-8 40M32 14c6 10 9 22 8 40" fill="none" stroke="#e6a9c2" stroke-width="2"/>'),
  "piment": sv('<path d="M18 22c10-6 26 2 28 18 1 8-4 16-13 20 6-8 3-20-6-24-4-2-8-2-10-1z" fill="#c9302a"/><path d="M18 22c-1-6 3-10 8-11l1 5z" fill="#2f6b3a"/>'),
  "arachide": sv('<path d="M22 10c8-4 16 1 14 9-1 5 4 8 7 12 6 8 2 22-9 22-10 0-13-10-8-18 3-5-2-8-4-12-2-4-2-9 0-13z" fill="#d1a15f" transform="rotate(20 32 32)"/><g fill="#a97b3d" opacity=".7"><circle cx="30" cy="22" r="1.5"/><circle cx="38" cy="30" r="1.5"/><circle cx="33" cy="42" r="1.5"/><circle cx="42" cy="46" r="1.5"/></g>'),
  "_sac": sv('<path d="M22 14c-2 8-8 14-8 26 0 11 8 18 18 18s18-7 18-18c0-12-6-18-8-26z" fill="#cda766"/><path d="M22 14h20l-2 6H24z" fill="#a98445"/><path d="M24 20q8 3 16 0" stroke="#8c6a31" stroke-width="2.4" fill="none" stroke-linecap="round"/>'),
};
const PHOTOS = { "haricot vert": "haricot-vert", "haricot": "haricot", "maïs": "mais", "tomate": "tomate", "gari": "gari", "igname": "igname", "riz": "riz" };
const CLES_PHOTOS = Object.keys(PHOTOS).sort((x, y) => y.length - x.length);
function picto(nom) {
  const n = norm(nom || "");
  if (!n.includes("farine")) {
    for (const k of CLES_PHOTOS) if (n.includes(norm(k))) return '<img src="/produits/' + PHOTOS[k] + '.jpg" alt="" width="64" height="64" decoding="async">';
  }
  for (const k of Object.keys(PICTOS)) if (k !== "_sac" && n.includes(norm(k))) return PICTOS[k];
  return PICTOS._sac;
}
const TUILES = ["maïs", "haricot", "gari", "igname", "tomate", "riz"];
function dessinerTuiles() {
  const box = $("tuiles"); box.replaceChildren();
  for (const p of TUILES) {
    const b = el("button", "tuile"); b.type = "button"; b.innerHTML = picto(p); b.append(el("span", "", cap(p)));
    b.addEventListener("click", () => {
      if (etat === "reflexion") return;
      cible = "parler"; debloquerAudio(); stopParole();
      const fd = new FormData(); fd.append("text", "Je vends du " + p + "."); demander(fd);
    });
    box.append(b);
  }
}
// ---------------------------------------------------------------- onglet Parler : affichage du résultat
function puce(libelle, valeur, cls) { const p = el("div", "puce " + (cls || "")); p.append(el("small", "", libelle), el("b", "", valeur)); return p; }

function afficher(r, demo) {
  const f = $("feuille"); f.replaceChildren();
  app.dataset.vue = (r.status === "needs_info" || r.status === "error") ? "question" : "resultat";
  if (demo && r.status !== "ok" && r.status !== "success") f.append(el("div", "demo-tag", "Exemple illustratif — chiffres fictifs"));
  const cite = r.transcription ? el("p", "cite", "Vous avez dit : « " + r.transcription + " »") : null;
  if (cite && (r.status === "needs_info" || r.status === "error")) f.append(cite);

  if (r.status === "needs_info") {
    const d = r.donnees || {};
    const c = el("div", "cle q"); c.append(el("small", "", "Dagbé vous demande"), el("strong", "", (r.questions && r.questions[0]) || r.reponse_texte));
    f.append(c);
    const pc = el("div", "puces");
    pc.append(puce("Produit", cap(r.produit_fr) || "à préciser", r.produit_fr ? "" : "vide"),
              puce("Marché", d.lieu || "à préciser", d.lieu ? "" : "vide"),
              puce("Quantité", d.quantite_kg ? F(d.quantite_kg) + " kg" : "à préciser", d.quantite_kg ? "" : "vide"));
    f.append(pc);
    if (r.notes && r.notes.length) f.append(el("p", "cite", r.notes.join(" ")));
    return;
  }
  if (r.status === "error") {
    const c = el("div", "cle q"); c.append(el("small", "", "Un instant"), el("strong", "", r.reponse_texte));
    f.append(c);
    return;
  }

  const v = r.verdict_offre;
  let cls = "neutre", titre = "Comparaison des marchés", sous = [cap(r.produit_fr), r.donnees && r.donnees.lieu].filter(Boolean).join(" · ");
  if (v) {
    const e = String(Math.abs(v.ecart_pct)).replace(".", ",");
    if (v.niveau === "inférieure") { cls = "rouge"; titre = "Offre trop basse"; sous = e + " % sous le prix du marché" + (v.sous_minimum_12_mois ? ", sous le minimum de l'année" : ""); }
    else if (v.niveau === "supérieure") { cls = "vert"; titre = "Bonne offre"; sous = e + " % au-dessus du prix du marché"; }
    else { cls = "ambre"; titre = "Offre correcte"; sous = "Proche du prix du marché (" + (v.ecart_pct >= 0 ? "+" : "−") + e + " %)"; }
  }
  const bloc = el("div", "bloc " + cls);
  const bt = el("div", "bloc-top"); bt.append(el("b", "", "Dagbé"));
  if (demo) bt.append(el("span", "demo-tag", "Exemple · chiffres fictifs"));
  const mb = el("button", "icone"); mb.type = "button"; mb.setAttribute("aria-label", "Réglages");
  mb.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h16M4 16h16"/></svg>';
  mb.addEventListener("click", () => $("btn-menu").click()); bt.append(mb); bloc.append(bt);
  const offre0 = r.donnees && r.donnees.prix_propose_kilo;
  const ctx = [cap(r.produit_fr), r.donnees && r.donnees.lieu, offre0 ? "offre " + F(offre0) + " F/kg" : null].filter(Boolean).join(" · ");
  bloc.append(el("p", "ctx", ctx), el("h2", "", titre), el("p", "sous", sous));
  const rs = r.resume;
  if (rs) {
    bloc.append(el("hr"));
    const g = el("div", "gain");
    if (rs.ailleurs) g.append(el("small", "", "Meilleur net : " + rs.meilleure), el("strong", "", "+" + F(rs.gain_fcfa) + " F"),
      el("span", "", "de plus que " + (rs.base_est_offre ? "votre offre" : "la vente sur place") + ", transport déduit (" + rs.distance_km + " km)"));
    else g.append(el("small", "", "Meilleure option"), el("strong", "", "Vendre sur place"), el("span", "", "Après transport, c'est le plus avantageux parmi les marchés comparés"));
    bloc.append(g);
  }
  f.append(bloc);

  if (cite) f.append(cite);
  const pc = el("div", "puces");
  if (r.reference) pc.append(puce("Prix du marché", F(r.reference.prix) + " F/kg"));
  const offre = r.donnees && r.donnees.prix_propose_kilo;
  if (offre) pc.append(puce("Votre offre", F(offre) + " F/kg"));
  if (rs && rs.marge_offre_fcfa != null) pc.append(puce("Votre marge", signe(rs.marge_offre_fcfa) + " F", rs.marge_offre_fcfa < 0 ? "moins" : "plus"));
  if (pc.children.length) f.append(pc);

  const det = el("details", "detail"); det.open = true; det.append(el("summary", "", "Comparer les marchés"));
  (r.options || []).forEach((o, i) => {
    const row = el("div", "opt" + (i === 0 && r.options.length > 1 && rs && rs.ailleurs ? " top" : ""));
    const n = el("div", "n"); n.append(el("b", "", o.marche), el("small", "", o.distance_km ? o.distance_km + " km · transport " + F(o.transport_fcfa) + " F" : "Sur place"));
    const val = el("div", "v"); val.append(el("b", "", F(o.net_total_fcfa) + " F"), el("small", "", F(o.prix_kg) + " F/kg"));
    row.append(n, val); det.append(row);
  });
  const notes = el("div", "notes");
  for (const l of (r.reponse_texte || "").split("\n")) if (/^(Hypothèses|Attention|\d+ contribution)/.test(l)) notes.append(el("p", "", l));
  if (r.reference) {
    const rf = r.reference;
    notes.append(el("p", "", rf.source === "collecte"
      ? "Source : contributions des 14 derniers jours (" + rf.n_contrib + ")" + (rf.prix_hdx ? ", comparées à HDX : " + F(rf.prix_hdx) + " F/kg en " + mois(rf.date_hdx) + "." : ".")
      : "Source : WFP via HDX HAPI · " + rf.marche + " · relevé en " + mois(rf.date) + "."));
    notes.append(el("p", "", "Fiabilité du prix de référence : " + rf.fiabilite + ". Net = revenu − transport estimé."));
  }
  det.append(notes); f.append(det);

  const act = el("div", "actions");
  const b1 = el("button", "btn plein"); b1.id = "btn-reecouter"; b1.type = "button"; b1.append(icone("son"), document.createTextNode("Réécouter"));
  b1.addEventListener("click", () => { if (derniereParole && etat === "repos") dire(derniereParole, false); });
  const b2 = el("button", "btn", "Nouvelle demande"); b2.type = "button"; b2.addEventListener("click", nouvelle);
  act.append(b1, b2); f.append(act);
  $("scene").scrollTop = 0;
}
function nouvelle() {
  stopParole(); annule = true; arreter(); dialogue = null;
  $("feuille").replaceChildren(); app.dataset.vue = "accueil"; setEtat("repos");
}

// ---------------------------------------------------------------- exemple (aucun appel serveur, aucune donnée réelle)
const EXEMPLE = {
  status: "ok", transcription: "On m'offre 150 francs le kilo de maïs à Allada, j'ai mille kilos. Ça me coûte 110 francs le kilo.",
  produit_fr: "maïs blanc", donnees: { lieu: "Allada", quantite_kg: 1000, prix_propose_kilo: 150, cout_revient_kilo: 110 },
  verdict_offre: { niveau: "inférieure", ecart_pct: -16.7, sous_minimum_12_mois: false },
  reference: { prix: 180, marche: "Marché exemple", date: "2026-10-31", source: "hdx", fiabilite: "Indicatif", n_contrib: 0 },
  resume: { meilleure: "Cotonou", distance_km: 45, ailleurs: true, gain_fcfa: 23250, base_est_offre: true, marge_offre_fcfa: 40000 },
  options: [
    { marche: "Cotonou", prix_kg: 180, distance_km: 45, transport_fcfa: 6750, net_total_fcfa: 173250 },
    { marche: "Allada (offre reçue)", prix_kg: 150, distance_km: 0, transport_fcfa: 0, net_total_fcfa: 150000 },
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
  stopParole(); dialogue = null; afficher(EXEMPLE, true); derniereParole = EXEMPLE.reponse_vocale;
  if (parler) { setEtat("parole", "Voici un exemple de réponse"); voixTelephone(EXEMPLE.reponse_vocale, () => setEtat("repos", "Appuyez pour parler")); }
}

// ---------------------------------------------------------------- listes (zones, produits)
let listes = null, chargementListes = null;
function chargerListes() {
  if (listes) return Promise.resolve(listes);
  if (!chargementListes) {
    chargementListes = fetch("/api/zones").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }).then((d) => {
      listes = d; remplirContribution(); if (zoneChoisie && app.dataset.onglet === "prix") afficherPrix(); return d;
    }).catch((e) => { chargementListes = null; $("prix-vide").textContent = "Impossible de charger les zones pour le moment (" + e.message + ")."; });
  }
  return chargementListes;
}

// ---------------------------------------------------------------- onglet Prix
let zoneChoisie = store.get("dagbe-zone"), produitFiltre = null, derniersPrix = null;
const voile = $("voile");
function ouvrirBas(id) { voile.hidden = false; $(id).hidden = false; }
function fermerBas() { voile.hidden = true; $("sel-zone").hidden = true; $("reglages").hidden = true; }
voile.addEventListener("click", fermerBas);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") fermerBas(); });

$("choisir-zone").addEventListener("click", async () => {
  await chargerListes(); if (!listes) return;
  $("zone-recherche").value = ""; listerZones(""); ouvrirBas("sel-zone"); $("zone-recherche").focus();
});
function norm(s) { return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
function listerZones(q) {
  const box = $("liste-zones"); box.replaceChildren();
  const n = norm(q.trim());
  for (const z of listes.zones.filter((z) => !n || norm(z.nom).includes(n))) {
    const b = el("button", "", z.nom); b.type = "button"; if (z.nom === zoneChoisie) b.setAttribute("aria-current", "true");
    b.addEventListener("click", () => { zoneChoisie = z.nom; produitFiltre = null; store.set("dagbe-zone", z.nom); fermerBas(); afficherPrix(); });
    box.append(b);
  }
  if (!box.children.length) box.append(el("p", "vide", "Aucune zone ne correspond."));
}
$("zone-recherche").addEventListener("input", (e) => listerZones(e.target.value));

async function afficherPrix() {
  if (!zoneChoisie) return;
  $("zone-libelle").textContent = zoneChoisie;
  const liste = $("liste-prix"); liste.replaceChildren();
  $("prix-vide").hidden = false; $("prix-vide").textContent = "Chargement des prix…"; $("prix-recherche").hidden = true; $("prix-note").hidden = true;
  let d;
  try {
    const rep = await fetch("/api/prix?zone=" + encodeURIComponent(zoneChoisie));
    if (!rep.ok) throw new Error(rep.status);
    d = await rep.json();
  } catch (e) { $("prix-vide").textContent = "Impossible de charger les prix (" + e.message + ")."; return; }
  derniersPrix = d; dessinerPrix();
}
let dateCommune = null;
function dessinerPrix() {
  const d = derniersPrix, liste = $("liste-prix"); liste.replaceChildren();
  const rech = $("prix-recherche");
  if (!d || !d.items.length) { $("prix-vide").hidden = false; $("prix-vide").textContent = "Aucun prix connu pour cette zone."; rech.hidden = true; return; }
  $("prix-vide").hidden = true; $("prix-note").hidden = false;
  rech.hidden = d.items.length < 6;
  const n = norm(rech.value.trim());
  const COURANTS = ["maïs", "haricot", "gari", "igname", "riz", "tomate", "niébé", "manioc", "oignon", "piment", "arachide", "sorgho", "huile"];
  const rang = (i) => { const k = COURANTS.findIndex((c) => norm(i.produit_fr).includes(norm(c))); return k < 0 ? 99 : k; };
  const dates = {}; for (const i of d.items) dates[i.date] = (dates[i.date] || 0) + 1;
  dateCommune = Object.keys(dates).sort((x, y) => dates[y] - dates[x])[0];
  d.items.sort((x, y) => rang(x) - rang(y) || x.produit_fr.localeCompare(y.produit_fr, "fr"));
  const items = d.items.filter((i) => !n || norm(i.produit_fr).includes(n) || norm(i.produit).includes(n));
  if (!items.length) { $("prix-vide").hidden = false; $("prix-vide").textContent = "Aucun produit ne correspond."; return; }
  for (const i of items) liste.append(ligne(i));
}
$("prix-recherche").addEventListener("input", dessinerPrix);
function ligne(i) {
  const c = el("details", "lp"), s = el("summary");
  const cls = { "Fiable": "fiable", "Peu de données": "peu", "Ancien": "ancien" }[i.fiabilite] || "";
  const pic = el("span", "picto"); pic.innerHTML = picto(i.produit_fr);
  const nom = el("div", "nom"); nom.append(el("b", "", cap(i.produit_fr)));
  const sm = el("small"); sm.append(el("span", "pt " + cls));
  sm.append(document.createTextNode(cls ? i.fiabilite : (i.source === "collecte" ? "Collecté" : (i.date !== dateCommune ? "Relevé " + mois(i.date) : "Relevé officiel"))));
  nom.append(sm);
  const p = el("span", "prix", F(i.prix) + " "); p.append(el("small", "", "F/kg"));
  const fl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  fl.setAttribute("viewBox", "0 0 24 24"); fl.setAttribute("class", "fleche");
  const pa = document.createElementNS("http://www.w3.org/2000/svg", "path"); pa.setAttribute("d", "M6 9l6 6 6-6"); fl.append(pa);
  s.append(pic, nom, p, fl); c.append(s);
  const corps = el("div", "corps-lp");
  corps.append(el("p", "", i.source === "collecte"
    ? "Collecté · " + i.n_contrib + " contribution" + (i.n_contrib > 1 ? "s" : "") + " · dernière le " + jour(i.date)
    : "HDX · relevé en " + mois(i.date) + (i.type === "Wholesale" ? " · prix de gros" : " · prix de détail")));
  if (i.source === "collecte" && i.prix_hdx) corps.append(el("p", "", "HDX : " + F(i.prix_hdx) + " F/kg (" + mois(i.date_hdx) + ")"));
  if (i.source === "hdx" && i.n_contrib) corps.append(el("p", "", i.n_contrib + " contribution(s) récente(s) à " + F(i.prix_collecte) + " F/kg, pas encore assez pour faire référence."));
  if (i.min_12_mois) corps.append(el("p", "", "Sur 12 mois : " + F(i.min_12_mois) + " à " + F(i.max_12_mois) + " F/kg"));
  const bt = el("button", "voisins-bt", "Voir les marchés voisins"); bt.type = "button";
  let zone = null;
  bt.addEventListener("click", async () => {
    if (zone) { zone.hidden = !zone.hidden; return; }
    bt.textContent = "Chargement…";
    try {
      const rep = await fetch("/api/prix?zone=" + encodeURIComponent(zoneChoisie) + "&produit=" + encodeURIComponent(i.produit));
      const d = await rep.json();
      zone = el("div", "voisins");
      if (!d.voisins || !d.voisins.length) zone.append(el("p", "", "Aucun marché voisin avec un prix récent (150 km maximum)."));
      for (const v of d.voisins || []) {
        const row = el("div", "voisin"), g = el("span", "", v.zone); g.append(el("small", "", "  " + v.distance_km + " km"));
        row.append(g, el("b", "", F(v.prix) + " F/kg")); zone.append(row);
      }
      corps.append(zone); bt.textContent = "Marchés voisins";
    } catch (e) { bt.textContent = "Voir les marchés voisins"; }
  });
  corps.append(bt); c.append(corps);
  return c;
}

// ---------------------------------------------------------------- onglet Contribuer
function remplirContribution() {
  const sp = $("c-produit"), sz = $("c-zone");
  if (sp.options.length > 1) return;
  for (const p of listes.produits) sp.append(new Option(cap(p.fr), p.id));
  for (const z of listes.zones) sz.append(new Option(z.nom, z.nom));
  if (zoneChoisie) sz.value = zoneChoisie;
  const box = $("c-tuiles"); box.replaceChildren();
  for (const nom of TUILES) {
    const o = [...sp.options].find((x) => x.value && norm(x.text).includes(norm(nom)));
    if (!o) continue;
    const b = el("button", "", ""); b.type = "button"; b.dataset.val = o.value; b.innerHTML = picto(nom); b.append(el("span", "", cap(nom)));
    b.addEventListener("click", () => { sp.value = o.value; majTuilesContrib(); });
    box.append(b);
  }
  sp.addEventListener("change", majTuilesContrib); majTuilesContrib();
}
function majTuilesContrib() {
  for (const b of $("c-tuiles").children) b.setAttribute("aria-pressed", String(b.dataset.val === $("c-produit").value));
}
function deviceId() {
  let d = store.get("dagbe-device");
  if (!d) { d = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)); store.set("dagbe-device", d); }
  return d;
}
function retour(msg, erreur) { const r = $("c-retour"); r.hidden = false; r.textContent = msg; r.classList.toggle("erreur", !!erreur); }
$("form-contrib").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const produit = $("c-produit").value, zone = $("c-zone").value, prix = $("c-prix").value.trim();
  if (!produit || !zone || !prix) return retour("Choisissez le produit, le marché et indiquez le prix.", true);
  const bt = $("c-envoyer"); bt.disabled = true;
  try {
    const rep = await fetch("/api/contribuer", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ produit, zone, prix, type: document.querySelector("input[name=c-type]:checked").value, device: deviceId() }) });
    const d = await rep.json().catch(() => ({}));
    retour(d.message || "Erreur " + rep.status, !rep.ok);
    if (rep.ok) { $("c-prix").value = ""; if (derniersPrix && zone === zoneChoisie) afficherPrix(); }
  } catch (e) { retour("Impossible d'envoyer pour le moment. Vérifiez votre connexion.", true); }
  bt.disabled = false;
});
$("mic-contrib").addEventListener("click", () => {
  if (cible !== "contrib" && etat !== "repos") return;
  cible = "contrib";
  if (etat === "ecoute") return arreter();
  if (etat === "repos") ecouter();
});
async function comprendreContribution(fd) {
  setEtat("reflexion");
  try {
    await chargerListes();
    const rep = await fetch("/api/extraire", { method: "POST", body: fd });
    const d = await rep.json();
    if (!rep.ok) { setEtat("repos", d.erreur || "Je n'ai pas compris. Remplissez le formulaire."); return; }
    if (d.produit) { $("c-produit").value = d.produit; majTuilesContrib(); }
    if (d.zone) $("c-zone").value = d.zone;
    if (d.prix) $("c-prix").value = String(Math.round(d.prix));
    const manque = [!d.produit && "le produit", !d.zone && "le marché", !d.prix && "le prix"].filter(Boolean);
    setEtat("repos", manque.length ? "J'ai compris : « " + d.transcription + " ». Il manque " + manque.join(", ") + "." : "Vérifiez puis touchez « Envoyer mon prix ».");
  } catch (e) { setEtat("repos", "Je n'arrive pas à joindre le serveur."); }
}

// ---------------------------------------------------------------- commandes générales
$("orbe").addEventListener("click", () => {
  cible = "parler";
  if (etat === "ecoute") return arreter();
  if (etat === "parole") { stopParole(); return setEtat("repos", "Appuyez pour parler"); }
  if (etat === "repos") ecouter();
});
function ouvrirSaisie() { $("form-texte").hidden = false; $("btn-ecrire").hidden = true; $("saisie").focus(); }
$("btn-ecrire").addEventListener("click", ouvrirSaisie);
$("btn-ecrire2").addEventListener("click", () => { fermerBas(); allerA("parler"); ouvrirSaisie(); });
$("form-texte").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const t = $("saisie").value.trim(); if (!t || etat === "reflexion") return;
  cible = "parler"; debloquerAudio(); stopParole(); const fd = new FormData(); fd.append("text", t); $("saisie").value = ""; demander(fd);
});
$("btn-menu").addEventListener("click", () => { ouvrirBas("reglages"); $("btn-fermer").focus(); });
$("btn-fermer").addEventListener("click", fermerBas);
$("opt-eco").checked = store.get("dagbe-eco") === "1";
$("opt-eco").addEventListener("change", (e) => store.set("dagbe-eco", e.target.checked ? "1" : "0"));
$("btn-exemple").addEventListener("click", () => { fermerBas(); allerA("parler"); debloquerAudio(); montrerExemple(true); });

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
  if (dejaInstallee || app.dataset.onglet !== "parler") return;
  if (!force && Number(store.get("dagbe-install-non") || 0) > Date.now() - 3 * 864e5) return;
  const b = $("install"), go = $("install-go"), titre = $("install-titre"), aide = $("install-aide");
  go.hidden = false; go.onclick = null; bandeauMontre = true;
  if (navIntegre) {
    titre.textContent = "Ouvrez Dagbé dans Chrome pour l'installer";
    aide.textContent = "Ce lien s'est ouvert dans une application (WhatsApp, Facebook…) qui ne permet pas l'installation.";
    etapes([`Touchez <b>${ICONES.menu}</b> en haut à droite`, "Choisissez « Ouvrir dans Chrome » ou « Ouvrir dans le navigateur »"]);
    go.textContent = "Copier le lien";
    go.onclick = async () => { try { await navigator.clipboard.writeText(location.origin); go.textContent = "Lien copié"; } catch (e) { go.textContent = location.origin; } };
  } else if (invite && !sim) {
    titre.textContent = "Installez Dagbé sur votre téléphone"; aide.textContent = "Accès direct depuis votre écran d'accueil, en plein écran."; etapes([]);
    go.textContent = "Installer";
    go.onclick = async () => { b.hidden = true; invite.prompt(); try { await invite.userChoice; } catch (e) {} invite = null; };
  } else if (ios) {
    titre.textContent = "Ajoutez Dagbé à votre écran d'accueil"; aide.textContent = "Sur iPhone, depuis Safari :";
    etapes([`Touchez <b>${ICONES.partage}</b> Partager (en bas de l'écran)`, "Choisissez « Sur l'écran d'accueil »", "Touchez « Ajouter »"]);
    go.textContent = "J'ai compris"; go.onclick = () => $("install-non").click();
  } else if (android) {
    titre.textContent = "Installez Dagbé sur votre téléphone"; aide.textContent = "Depuis Chrome :";
    etapes([`Touchez <b>${ICONES.menu}</b> en haut à droite`, "Choisissez « Installer l'application » (ou « Ajouter à l'écran d'accueil »)"]);
    go.textContent = "J'ai compris"; go.onclick = () => $("install-non").click();
  } else return;
  b.hidden = false; $("btn-installer").hidden = false;
}
$("install-non").addEventListener("click", () => { $("install").hidden = true; store.set("dagbe-install-non", String(Date.now())); });
$("btn-installer").addEventListener("click", () => { fermerBas(); allerA("parler"); montrerBandeau(true); });
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); invite = e; montrerBandeau(); });
window.addEventListener("appinstalled", () => { $("install").hidden = true; $("btn-installer").hidden = true; invite = null; });
if (!dejaInstallee) {
  setTimeout(() => { if (!bandeauMontre && (ios || navIntegre || sim)) montrerBandeau(!!sim); }, 900);
  setTimeout(() => { if (!bandeauMontre && android) montrerBandeau(); }, 3500);
}

// ---------------------------------------------------------------- démarrage
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
window.addEventListener("offline", () => setEtat(etat, "Pas de connexion"));
window.addEventListener("online", () => etat === "repos" && setEtat("repos"));

const ex = params.get("exemple");                       // aperçus : ?exemple, ?exemple=question
if (ex !== null) { if (ex === "question") afficher(EXEMPLE_QUESTION); else montrerExemple(false); setEtat("repos"); }
const onglet = params.get("onglet");                    // ?onglet=prix|contribuer (aperçu et liens directs)
if (onglet && VUES[onglet]) allerA(onglet);
if (params.get("zone")) zoneChoisie = params.get("zone");

fetch("/api/health").then((r) => r.json()).then((h) => {
  if (h.base && h.base.ok && h.asr && h.llm) return;
  const manque = [];
  if (!h.base || !h.base.ok) manque.push("base de prix");
  if (!h.asr) manque.push("clé Groq");
  if (!h.llm) manque.push("clé Gemini");
  $("statut").textContent = "Configuration incomplète : " + manque.join(", ");
}).catch(() => {});
dessinerTuiles();
