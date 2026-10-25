// Radar de stages — tableau de bord (application d'une page, sans build).
// Données : Supabase (connexion par e-mail + mot de passe ; règles RLS côté base).
import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm";

const CFG = window.RADAR_CONFIG || {};
const sb = createClient(CFG.SUPABASE_URL, CFG.SUPABASE_ANON_KEY);
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LIST_COLS = "id,title,company,company_norm,location,url,score,priority,domain,domain_label,published_at,published_is_estimate,detected_at,is_internship,duration,start_date,languages,active,status,favorite,feedback,applied_at,answered_at,next_action,next_action_at,first_source_id,manual,initial_import";

const state = { jobs: [], sources: [], settings: null, statuses: [], filters: loadFilters() };

// ------------------------------------------------------------------ utilitaires
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2600); }
function fmtDate(v, time = false) { if (!v) return "—"; const d = new Date(v); return time ? d.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString("fr-FR"); }
function ago(v) { if (!v) return ""; const s = (Date.now() - new Date(v)) / 1000; if (s < 3600) return `il y a ${Math.max(1, Math.round(s / 60))} min`; if (s < 86400) return `il y a ${Math.round(s / 3600)} h`; return `il y a ${Math.round(s / 86400)} j`; }
function days(a, b) { return (new Date(b) - new Date(a)) / 86400000; }
function statusLabel(k) { return (state.statuses.find((s) => s.key === k) || { label: k }).label; }
function loadFilters() { try { return JSON.parse(localStorage.getItem("radar-filters")) || {}; } catch { return {}; } }
function saveFilters() { try { localStorage.setItem("radar-filters", JSON.stringify(state.filters)); } catch {} }
function srcName(id) { return (state.sources.find((s) => s.id === id) || { name: id || "Manuel" }).name; }
async function must(p) { const { data, error } = await p; if (error) { toast("Erreur : " + error.message); throw error; } return data; }

// ------------------------------------------------------------------ connexion
async function boot() {
  if (!CFG.SUPABASE_URL) { document.body.innerHTML = '<div class="login"><div class="card pad">Configuration manquante : renseigne les variables SUPABASE_URL et SUPABASE_ANON_KEY du dépôt GitHub puis relance le workflow « Publier le tableau de bord ».</div></div>'; return; }
  const { data } = await sb.auth.getSession();
  if (!data.session) return showLogin();
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  await loadAll();
  window.addEventListener("hashchange", route);
  route();
  // rafraîchissement en direct quand le robot ajoute des offres
  sb.channel("jobs").on("postgres_changes", { event: "*", schema: "public", table: "jobs" }, debounce(async () => { await loadJobs(); route(true); }, 1500)).subscribe();
}
function showLogin() {
  $("#login").classList.remove("hidden");
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const { error } = await sb.auth.signInWithPassword({ email: $("#login-email").value, password: $("#login-pass").value });
    if (error) { $("#login-error").textContent = "Identifiants incorrects."; return; }
    location.reload();
  };
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function loadAll() {
  const [settings, sources] = await Promise.all([
    must(sb.from("settings").select("*").eq("key", "profile").maybeSingle()),
    must(sb.from("sources").select("*").order("id")),
  ]);
  state.settings = settings?.value || {};
  state.statuses = state.settings.statuses || [];
  state.sources = sources;
  await loadJobs();
}
async function loadJobs() {
  const rows = [];
  for (let from = 0; ; from += 1000) {
    const batch = await must(sb.from("jobs").select(LIST_COLS).order("detected_at", { ascending: false }).range(from, from + 999));
    rows.push(...batch); if (batch.length < 1000) break;
  }
  state.jobs = rows;
}

// ------------------------------------------------------------------ navigation
function route(soft = false) {
  const h = location.hash.slice(1) || "home";
  const m = h.match(/^job=(\d+)/);
  if (m) { openJob(+m[1]); if (!$("#view").innerHTML) renderView("home"); return; }
  closeDrawer(false);
  renderView(h.split("&")[0]);
  if (!soft) window.scrollTo(0, 0);
}
function renderView(v) {
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === v));
  const views = { home: viewHome, offers: viewOffers, pipeline: viewPipeline, stats: viewStats, sources: viewSources, settings: viewSettings };
  (views[v] || viewHome)();
  state.view = v;
}

// ------------------------------------------------------------------ cartes d'offre
function jobCard(j) {
  const tags = [];
  if (j.status && j.status !== "nouvelle") tags.push(`<span class="tag status">${esc(statusLabel(j.status))}</span>`);
  if (!j.active) tags.push('<span class="tag warn">expirée</span>');
  if (!j.is_internship) tags.push('<span class="tag">hors stage</span>');
  if (j.duration) tags.push(`<span class="tag">${esc(j.duration)}</span>`);
  if (j.start_date) tags.push(`<span class="tag">début ${esc(j.start_date)}</span>`);
  if (j.languages?.length) tags.push(`<span class="tag">${esc(j.languages.join("/"))}</span>`);
  return `<div class="card job ${j.active ? "" : "expired"}" data-id="${j.id}">
    <div class="score ${esc(j.priority)}">${j.score ?? "?"}</div>
    <div><div class="t">${j.favorite ? '<span class="fav">★</span> ' : ""}${esc(j.title)}</div>
      <div class="m">${esc(j.company || "?")} · ${esc(j.location || "")} · ${esc(j.domain_label || "")}</div>
      <div>${tags.join("")}</div></div>
    <div class="side">${ago(j.detected_at)}<br>${esc(srcName(j.first_source_id))}</div></div>`;
}
function bindCards(root) { root.querySelectorAll("[data-id]").forEach((el) => (el.onclick = () => (location.hash = "job=" + el.dataset.id))); }

// ------------------------------------------------------------------ accueil
function viewHome() {
  const th = state.settings.thresholds || { priority: 60, interesting: 35 };
  const now = Date.now();
  const active = state.jobs.filter((j) => j.active && j.is_internship);
  const fresh = active.filter((j) => now - new Date(j.detected_at) < 48 * 3600e3 && !j.initial_import);
  const prio = active.filter((j) => j.score >= th.priority && ["nouvelle", "a_examiner"].includes(j.status));
  const inProgress = state.jobs.filter((j) => ["en_preparation", "envoyee", "entretien"].includes(j.status));
  const endToday = new Date(); endToday.setHours(23, 59, 59);
  const due = state.jobs.filter((j) => j.next_action_at && new Date(j.next_action_at) <= endToday && !["refus", "abandonnee", "offre"].includes(j.status));
  const broken = state.sources.filter((s) => s.enabled && s.last_status === "error");
  const lastRun = state.sources.map((s) => s.last_run_at).filter(Boolean).sort().pop();
  $("#view").innerHTML = `
    <h1>Bonjour Victor 👋</h1>
    <div class="kpis">
      <div class="card kpi"><div class="v">${fresh.length}</div><div class="l">nouvelles offres (48 h)</div></div>
      <div class="card kpi"><div class="v">${prio.length}</div><div class="l">prioritaires à traiter</div></div>
      <div class="card kpi"><div class="v">${inProgress.length}</div><div class="l">candidatures en cours</div></div>
      <div class="card kpi"><div class="v">${due.length}</div><div class="l">rappels aujourd'hui</div></div>
    </div>
    <p class="muted small">Dernier passage du radar : ${lastRun ? fmtDate(lastRun, true) + " (" + ago(lastRun) + ")" : "jamais"}${broken.length ? ` · <a href="#sources">⚠️ ${broken.length} source(s) en erreur</a>` : ""}</p>
    ${due.length ? `<h2>⏰ Rappels</h2><div class="jobs" id="due">${due.map((j) => jobCard(j).replace('class="side">', `class="side"><b>${esc(j.next_action || "Relance")}</b><br>${fmtDate(j.next_action_at)}<br>`)).join("")}</div>` : ""}
    <h2>🔥 Prioritaires à traiter</h2><div class="jobs" id="prio">${prio.slice(0, 15).map(jobCard).join("") || '<div class="card empty">Rien pour l\'instant.</div>'}</div>
    <h2>🆕 Détectées ces dernières 48 h</h2><div class="jobs" id="fresh">${fresh.sort((a, b) => b.score - a.score).slice(0, 30).map(jobCard).join("") || '<div class="card empty">Aucune nouvelle offre depuis 48 h.</div>'}</div>`;
  bindCards($("#view"));
}

// ------------------------------------------------------------------ toutes les offres
function viewOffers() {
  const f = state.filters;
  const companies = [...new Set(state.jobs.map((j) => j.company).filter(Boolean))].sort();
  const domains = [...new Set(state.jobs.map((j) => j.domain_label).filter(Boolean))].sort();
  $("#view").innerHTML = `
    <h1>Offres détectées</h1>
    <div class="card filters">
      <input class="wide" id="f-q" placeholder="Rechercher (titre, entreprise, lieu)" value="${esc(f.q || "")}">
      <select id="f-min"><option value="0">Tous scores</option>${[35, 50, 60, 75].map((v) => `<option value="${v}" ${f.min == v ? "selected" : ""}>Score ≥ ${v}</option>`).join("")}</select>
      <select id="f-status"><option value="">Tous statuts</option>${state.statuses.map((s) => `<option value="${s.key}" ${f.status === s.key ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select>
      <select id="f-source"><option value="">Toutes sources</option>${state.sources.map((s) => `<option value="${s.id}" ${f.source === s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}<option value="__manual" ${f.source === "__manual" ? "selected" : ""}>Ajout manuel</option></select>
      <select id="f-days"><option value="">Toutes dates</option>${[1, 3, 7, 30].map((d) => `<option value="${d}" ${f.days == d ? "selected" : ""}>${d === 1 ? "24 h" : d + " jours"}</option>`).join("")}</select>
      <select id="f-domain"><option value="">Tous domaines</option>${domains.map((d) => `<option ${f.domain === d ? "selected" : ""}>${esc(d)}</option>`).join("")}</select>
      <select id="f-company"><option value="">Toutes entreprises</option>${companies.map((c) => `<option ${f.company === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <input id="f-loc" placeholder="Lieu" value="${esc(f.loc || "")}">
      <select id="f-sort"><option value="score" ${f.sort !== "date" && f.sort !== "pub" ? "selected" : ""}>Tri : score</option><option value="date" ${f.sort === "date" ? "selected" : ""}>Tri : détection</option><option value="pub" ${f.sort === "pub" ? "selected" : ""}>Tri : publication</option></select>
      <div class="row wide">
        <button class="btn small ${f.fav ? "on" : ""}" id="f-fav">★ Favoris</button>
        <button class="btn small ${f.expired ? "on" : ""}" id="f-exp">Inclure expirées</button>
        <button class="btn small ${f.all ? "on" : ""}" id="f-all">Inclure hors stage</button>
      </div>
    </div>
    <div class="muted small" id="count"></div>
    <div class="jobs" id="list"></div>`;
  const set = (k, v) => { state.filters[k] = v; saveFilters(); renderList(); };
  $("#f-q").oninput = debounce((e) => set("q", e.target.value), 200);
  $("#f-loc").oninput = debounce((e) => set("loc", e.target.value), 200);
  for (const k of ["min", "status", "source", "days", "domain", "company", "sort"]) $("#f-" + k).onchange = (e) => set(k, e.target.value);
  for (const [id, k] of [["fav", "fav"], ["exp", "expired"], ["all", "all"]]) $("#f-" + id).onclick = (e) => { e.target.classList.toggle("on"); set(k, !state.filters[k]); };
  renderList();
}
function renderList() {
  const f = state.filters, q = (f.q || "").toLowerCase(), loc = (f.loc || "").toLowerCase();
  let rows = state.jobs.filter((j) =>
    (f.expired || j.active) && (f.all || j.is_internship) && (!f.fav || j.favorite) &&
    (j.score ?? 0) >= +(f.min || 0) && (!f.status || j.status === f.status) &&
    (!f.source || (f.source === "__manual" ? j.manual : j.first_source_id === f.source)) &&
    (!f.days || Date.now() - new Date(j.detected_at) < f.days * 86400e3) &&
    (!f.domain || j.domain_label === f.domain) && (!f.company || j.company === f.company) &&
    (!loc || (j.location || "").toLowerCase().includes(loc)) &&
    (!q || `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q)));
  const key = f.sort === "date" ? (j) => -new Date(j.detected_at) : f.sort === "pub" ? (j) => -new Date(j.published_at || j.detected_at) : (j) => -(j.score ?? 0);
  rows.sort((a, b) => key(a) - key(b));
  $("#count").textContent = `${rows.length} offre(s)`;
  $("#list").innerHTML = rows.slice(0, 300).map(jobCard).join("") || '<div class="card empty">Aucune offre ne correspond à ces filtres.</div>';
  bindCards($("#list"));
}

// ------------------------------------------------------------------ fiche détaillée
async function openJob(id) {
  const [job, links] = await Promise.all([
    must(sb.from("jobs").select("*").eq("id", id).maybeSingle()),
    must(sb.from("job_links").select("*").eq("job_id", id)),
  ]);
  if (!job) { toast("Offre introuvable"); return; }
  const a = job.analysis || {};
  const p = $("#drawer-panel");
  const fb = (k, label) => `<button class="btn small ${job.feedback === k ? "on" : ""}" data-fb="${k}">${label}</button>`;
  p.innerHTML = `
    <div class="row" style="justify-content:space-between"><button class="btn ghost small" data-close>← Fermer</button>
      <a class="btn primary small" href="${esc(job.url)}" target="_blank" rel="noopener">Voir l'annonce originale ↗</a></div>
    <h1>${esc(job.title)}</h1>
    <div class="muted">${esc(job.company || "?")} · ${esc(job.location || "")}</div>
    <div style="margin:8px 0">${job.active ? "" : `<span class="tag warn">expirée le ${fmtDate(job.expired_at)}</span>`}
      <span class="tag ${job.priority === "prioritaire" ? "hot" : job.priority === "interessante" ? "ok" : ""}">${job.score ?? "?"}/100 · ${esc(job.priority || "")}</span>
      ${job.duration ? `<span class="tag">${esc(job.duration)}</span>` : ""}${job.start_date ? `<span class="tag">début ${esc(job.start_date)}</span>` : ""}
      ${job.languages?.length ? `<span class="tag">${esc(job.languages.join("/"))}</span>` : ""}<span class="tag">${esc(job.domain_label || "")}</span></div>
    <div class="small muted">Publiée : ${job.published_at ? (job.published_is_estimate ? "≈ " : "") + fmtDate(job.published_at, job.published_is_estimate) : "non indiquée"} · Détectée : ${fmtDate(job.detected_at, true)}${job.initial_import ? " (déjà en ligne à l'activation du radar)" : ""}</div>

    <div class="card pad" style="margin-top:14px">
      <div class="grid2">
        <label>Statut<select id="j-status">${state.statuses.map((s) => `<option value="${s.key}" ${job.status === s.key ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select></label>
        <label>Prochaine action<div class="row"><input id="j-na" placeholder="Relancer, préparer l'entretien…" value="${esc(job.next_action || "")}"></div></label>
        <label>Date de la prochaine action<input type="datetime-local" id="j-nad" value="${job.next_action_at ? new Date(new Date(job.next_action_at) - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ""}"></label>
        <label>Candidature envoyée le<input type="date" id="j-applied" value="${job.applied_at ? job.applied_at.slice(0, 10) : ""}"></label>
      </div>
      <label>Notes<textarea id="j-notes" placeholder="Contacts, questions d'entretien, impressions…">${esc(job.notes || "")}</textarea></label>
      <div class="actions">
        <button class="btn primary small" id="j-save">Enregistrer</button>
        <button class="btn small ${job.favorite ? "on" : ""}" id="j-fav">★ Favori</button>
        ${job.next_action_at ? '<button class="btn small" id="j-ics">📅 Ajouter au calendrier</button>' : ""}
      </div>
      <div class="small muted">Ton avis (ajuste les futurs scores de cette entreprise) :</div>
      <div class="actions">${fb("top", "🎯 Très pertinente")}${fb("like", "👍 Intéressante")}${fb("dislike", "👎 Hors cible")}</div>
    </div>

    ${a.reasons?.length ? `<h2>Pourquoi cette offre</h2><ul>${a.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    ${a.details?.length ? `<h2>Détail du score</h2><table class="breakdown">${a.details.map((d) => `<tr><td>${esc(d.label)}<div class="muted">${esc(d.why || "")}</div></td><td class="p ${d.points < 0 ? "neg" : ""}">${d.points > 0 ? "+" : ""}${d.points}</td></tr>`).join("")}</table>` : ""}
    ${a.missions?.length ? `<h2>Missions</h2><ul>${a.missions.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
    ${a.requirements?.length ? `<h2>Prérequis</h2><ul>${a.requirements.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
    <h2>Où l'offre a été vue</h2>
    <table class="data">${links.map((l) => `<tr><td>${esc(srcName(l.source_id))}</td><td><a href="${esc(l.url)}" target="_blank" rel="noopener">lien ↗</a></td><td class="muted">vue le ${fmtDate(l.first_seen_at, true)}${l.active ? "" : " · retirée"}</td></tr>`).join("") || '<tr><td class="muted">Ajout manuel</td></tr>'}</table>
    <h2>Pistes de contacts</h2>
    <p class="small muted">Recherches préparées (rien n'est collecté automatiquement) : ouvre-les, vérifie toi-même les profils, puis note tes contacts ci-dessus.</p>
    <div class="actions">
      ${contactSearch(`site:linkedin.com/in "${job.company}" Luxembourg SKEMA`, "Alumni SKEMA")}
      ${contactSearch(`site:linkedin.com/in "${job.company}" Luxembourg "ICN"`, "Alumni ICN")}
      ${contactSearch(`site:linkedin.com/in "${job.company}" Luxembourg (recruiter OR "talent acquisition" OR HR)`, "Recruteurs / RH")}
      ${contactSearch(`site:linkedin.com/in "${job.company}" Luxembourg ${(job.domain_label || "").split("(")[0].split("—").pop().trim()}`, "Équipe métier")}
      ${contactSearch(`site:linkedin.com/in "${job.company}" Luxembourg (intern OR stagiaire OR trainee)`, "Anciens stagiaires")}
    </div>
    <h2>Description</h2><div class="card pad desc">${esc(job.description || "—")}</div>`;
  $("#drawer").classList.remove("hidden");
  p.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => closeDrawer()));
  $("#drawer .drawer-backdrop").onclick = () => closeDrawer();
  $("#j-save").onclick = async () => {
    const nad = $("#j-nad").value, applied = $("#j-applied").value;
    await must(sb.from("jobs").update({ status: $("#j-status").value, next_action: $("#j-na").value || null, next_action_at: nad ? new Date(nad).toISOString() : null, notes: $("#j-notes").value, applied_at: applied ? new Date(applied).toISOString() : null }).eq("id", id));
    await loadJobs(); toast("Enregistré"); renderView(state.view || "home"); openJob(id);
  };
  $("#j-fav").onclick = async () => { await must(sb.from("jobs").update({ favorite: !job.favorite }).eq("id", id)); await loadJobs(); openJob(id); };
  p.querySelectorAll("[data-fb]").forEach((b) => (b.onclick = async () => { const v = job.feedback === b.dataset.fb ? null : b.dataset.fb; await must(sb.from("jobs").update({ feedback: v }).eq("id", id)); toast(v ? "Merci ! Le classement en tiendra compte." : "Avis retiré"); await loadJobs(); openJob(id); }));
  if ($("#j-ics")) $("#j-ics").onclick = () => downloadIcs(job);
}
function contactSearch(q, label) { return `<a class="btn small" target="_blank" rel="noopener" href="https://www.google.com/search?q=${encodeURIComponent(q)}">${esc(label)} ↗</a>`; }
function closeDrawer(updateHash = true) { $("#drawer").classList.add("hidden"); if (updateHash && location.hash.startsWith("#job=")) history.replaceState(null, "", "#" + (state.view || "home")); }
function downloadIcs(job) {
  const d = new Date(job.next_action_at), z = (x) => x.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Radar//FR", "BEGIN:VEVENT", `UID:radar-${job.id}-${d.getTime()}`, `DTSTAMP:${z(new Date())}`, `DTSTART:${z(d)}`, `DTEND:${z(new Date(d.getTime() + 1800e3))}`, `SUMMARY:${(job.next_action || "Relance")} — ${job.company}`, `DESCRIPTION:${job.title} ${job.url}`, "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([ics], { type: "text/calendar" })); a.download = "rappel.ics"; a.click();
}

// ------------------------------------------------------------------ ajout manuel
$("#add-btn").onclick = () => {
  const p = $("#drawer-panel");
  p.innerHTML = `<div class="row" style="justify-content:space-between"><button class="btn ghost small" data-close>← Fermer</button></div>
    <h1>Ajouter une offre</h1><p class="muted small">Pour une offre trouvée ailleurs (salon, contact, LinkedIn…). Le score sera calculé au prochain passage du radar.</p>
    <form id="add-form" class="card pad">
      <label>Intitulé *<input name="title" required></label>
      <div class="grid2"><label>Entreprise *<input name="company" required></label><label>Lieu<input name="location" value="Luxembourg"></label></div>
      <label>Lien de l'annonce *<input name="url" type="url" required></label>
      <label>Statut<select name="status">${state.statuses.map((s) => `<option value="${s.key}">${esc(s.label)}</option>`).join("")}</select></label>
      <label>Description (copier-coller)<textarea name="description"></textarea></label>
      <button class="btn primary" type="submit" style="margin-top:12px">Ajouter</button></form>`;
  $("#drawer").classList.remove("hidden");
  p.querySelector("[data-close]").onclick = () => closeDrawer();
  $("#add-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const row = await must(sb.from("jobs").insert({ ...f, dedup_key: "manual|" + Date.now(), manual: true, is_internship: true, score: null, priority: "interessante", detected_at: new Date().toISOString(), notified_at: new Date().toISOString() }).select().single());
    await loadJobs(); toast("Offre ajoutée"); location.hash = "job=" + row.id;
  };
};

// ------------------------------------------------------------------ candidatures (kanban)
function viewPipeline() {
  const cols = state.statuses.filter((s) => s.key !== "nouvelle");
  const tracked = state.jobs.filter((j) => j.status && j.status !== "nouvelle");
  $("#view").innerHTML = `<h1>Suivi des candidatures</h1>
    <p class="muted small">Change le statut depuis la fiche d'une offre. Passer en « Candidature envoyée » programme automatiquement une relance à J+10.</p>
    <div class="kanban">${cols.map((c) => { const items = tracked.filter((j) => j.status === c.key); return `<div class="col"><h3><span>${esc(c.label)}</span><span class="muted">${items.length}</span></h3>${items.map((j) => `<div class="kcard" data-id="${j.id}"><div class="t">${esc(j.title)}</div><div class="muted">${esc(j.company || "")}</div>${j.next_action_at ? `<div class="small">⏰ ${esc(j.next_action || "")} ${fmtDate(j.next_action_at)}</div>` : ""}${j.applied_at ? `<div class="small muted">envoyée le ${fmtDate(j.applied_at)}</div>` : ""}</div>`).join("")}</div>`; }).join("")}</div>`;
  bindCards($("#view"));
}

// ------------------------------------------------------------------ statistiques
async function viewStats() {
  const th = state.settings.thresholds || { interesting: 35 };
  const waitDays = state.settings.stats?.no_answer_after_days || 21;
  const hist = await must(sb.from("status_history").select("job_id,status,changed_at"));
  const reached = (st) => new Set(hist.filter((h) => h.status === st).map((h) => h.job_id));
  const interviews = reached("entretien"), offers = reached("offre"), refusals = reached("refus");
  const J = state.jobs;
  const applied = J.filter((j) => j.applied_at);
  const answered = applied.filter((j) => j.answered_at || interviews.has(j.id) || offers.has(j.id) || refusals.has(j.id));
  const pendingRecent = applied.filter((j) => !answered.includes(j) && days(j.applied_at, new Date()) < waitDays && j.status !== "abandonnee");
  const noAnswer = applied.filter((j) => !answered.includes(j) && days(j.applied_at, new Date()) >= waitDays);
  const mature = applied.length - pendingRecent.length;
  const pct = (a, b) => (b ? Math.round((100 * a) / b) + " %" : "—");
  const delays = answered.filter((j) => j.answered_at).map((j) => days(j.applied_at, j.answered_at));
  const avgDelay = delays.length ? (delays.reduce((a, b) => a + b, 0) / delays.length).toFixed(1) + " j" : "—";
  const relevant = J.filter((j) => j.is_internship && (j.score ?? 0) >= th.interesting);
  const group = (keyFn) => { const m = new Map(); for (const j of J) { const k = keyFn(j) || "—"; const g = m.get(k) || { det: 0, rel: 0, app: 0, ans: 0, itw: 0 }; g.det++; if (j.is_internship && (j.score ?? 0) >= th.interesting) g.rel++; if (j.applied_at) g.app++; if (answered.includes(j)) g.ans++; if (interviews.has(j.id)) g.itw++; m.set(k, g); } return [...m.entries()].sort((a, b) => b[1].app - a[1].app || b[1].rel - a[1].rel); };
  const table = (title, rows) => `<h2>${title}</h2><div class="card scroll"><table class="data"><tr><th></th><th>Détectées</th><th>Pertinentes</th><th>Candidatures</th><th>Réponses</th><th>Entretiens</th></tr>${rows.slice(0, 15).map(([k, g]) => `<tr><td>${esc(k)}</td><td>${g.det}</td><td>${g.rel}</td><td>${g.app}</td><td>${g.ans}</td><td>${g.itw}</td></tr>`).join("")}</table></div>`;
  const kpi = (v, l, h = "") => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div>${h ? `<div class="h">${h}</div>` : ""}</div>`;
  $("#view").innerHTML = `<h1>Statistiques</h1>
    <div class="kpis">
      ${kpi(J.length, "offres détectées")}${kpi(relevant.length, "stages pertinents", `score ≥ ${th.interesting}`)}
      ${kpi(applied.length, "candidatures envoyées")}${kpi(answered.length, "réponses reçues", "entretien, offre ou refus")}
      ${kpi(interviews.size, "entretiens")}${kpi(offers.size, "offres reçues")}${kpi(refusals.size, "refus")}
      ${kpi(pendingRecent.length, "en attente (récentes)", `envoyées il y a < ${waitDays} j`)}${kpi(noAnswer.length, "sans réponse", `≥ ${waitDays} j sans retour`)}
      ${kpi(pct(answered.length, mature), "taux de réponse", "sur les candidatures « mûres »")}
      ${kpi(pct(interviews.size, applied.length), "candidature → entretien")}${kpi(pct(offers.size, interviews.size), "entretien → offre")}
      ${kpi(avgDelay, "délai moyen de réponse")}
    </div>
    <p class="small muted">Le taux de réponse exclut les candidatures envoyées depuis moins de ${waitDays} jours (elles n'ont pas encore eu le temps d'obtenir une réponse). Réglable dans Paramètres.</p>
    ${table("Par source", group((j) => (j.manual ? "Ajout manuel" : srcName(j.first_source_id))))}
    ${table("Par type de poste", group((j) => j.domain_label))}
    ${table("Par entreprise", group((j) => j.company))}`;
}

// ------------------------------------------------------------------ sources
async function viewSources() {
  const runs = await must(sb.from("source_runs").select("*").order("started_at", { ascending: false }).limit(60));
  state.sources = await must(sb.from("sources").select("*").order("id"));
  $("#view").innerHTML = `<h1>Sources surveillées</h1>
    <p class="muted small">Le radar passe toutes les 20 minutes environ ; chaque source a sa propre fréquence. Une alerte Telegram est envoyée si une source échoue plusieurs fois de suite.</p>
    <div class="card scroll"><table class="data"><tr><th>Source</th><th>État</th><th>Dernier passage</th><th>Offres lues</th><th>Fréquence</th><th>Active</th></tr>
    ${state.sources.map((s) => `<tr><td><b>${esc(s.name)}</b><div class="muted">${esc(s.kind)}${s.target ? " · entreprise cible" : ""}</div></td>
      <td><span class="dot ${s.last_status || ""}"></span>${s.last_status === "error" ? `erreur (${s.consecutive_failures}×)<div class="muted small">${esc((s.last_error || "").slice(0, 140))}</div>` : s.last_status || "jamais lancée"}</td>
      <td>${s.last_run_at ? ago(s.last_run_at) : "—"}<div class="muted small">succès : ${s.last_success_at ? ago(s.last_success_at) : "—"}</div></td>
      <td>${s.last_found ?? "—"}</td>
      <td><select data-int="${esc(s.id)}">${[15, 20, 30, 60, 120, 360].map((m) => `<option value="${m}" ${s.interval_minutes == m ? "selected" : ""}>${m} min</option>`).join("")}</select></td>
      <td><input type="checkbox" data-en="${esc(s.id)}" ${s.enabled ? "checked" : ""} style="width:auto"></td></tr>`).join("")}</table></div>
    <h2>Ajouter une source</h2>
    <form id="src-form" class="card pad">
      <div class="grid2"><label>Identifiant (sans espace)<input name="id" required pattern="[a-z0-9-]+"></label><label>Nom<input name="name" required></label></div>
      <div class="grid2"><label>Type<select name="kind"><option value="workday">workday (site carrière Workday)</option><option value="links">links (page HTML d'offres)</option><option value="moovijob">moovijob (page de liste Moovijob)</option></select></label>
      <label>Entreprise cible ?<select name="target"><option value="true">oui</option><option value="false">non</option></select></label></div>
      <label>Configuration (JSON)<textarea name="config" id="src-config" style="font-family:monospace;min-height:120px">{"host": "xxx.wd3.myworkdayjobs.com", "tenant": "xxx", "site": "Careers", "searches": ["Luxembourg"], "location_must_contain": ["luxemb"]}</textarea></label>
      <p class="small muted">Workday : ouvre le site carrière, l'URL est de la forme https://<b>tenant</b>.wd3.myworkdayjobs.com/<b>site</b>. Page HTML : {"list_urls": ["https://…"], "link_pattern": "/job/…", "base_url": "https://…", "company": "Nom"}. Teste-la ensuite sur GitHub : Actions → Radar → Run workflow → mode « check-source ».</p>
      <button class="btn primary" type="submit">Ajouter</button></form>
    <h2>Derniers passages</h2>
    <div class="card scroll"><table class="data"><tr><th>Quand</th><th>Source</th><th>Résultat</th><th>Lues</th><th>Nouvelles</th></tr>
    ${runs.map((r) => `<tr><td>${fmtDate(r.started_at, true)}</td><td>${esc(srcName(r.source_id))}</td><td><span class="dot ${r.ok ? "ok" : "error"}"></span>${r.ok ? "ok" : esc((r.error || "").slice(0, 90))}</td><td>${r.found ?? ""}</td><td>${r.new_jobs ?? ""}</td></tr>`).join("")}</table></div>`;
  document.querySelectorAll("[data-en]").forEach((c) => (c.onchange = async () => { await must(sb.from("sources").update({ enabled: c.checked }).eq("id", c.dataset.en)); toast(c.checked ? "Source activée" : "Source désactivée"); }));
  document.querySelectorAll("[data-int]").forEach((c) => (c.onchange = async () => { await must(sb.from("sources").update({ interval_minutes: +c.value }).eq("id", c.dataset.int)); toast("Fréquence mise à jour"); }));
  $("#src-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    let config; try { config = JSON.parse(f.config); } catch { toast("Configuration JSON invalide"); return; }
    await must(sb.from("sources").insert({ id: f.id, name: f.name, kind: f.kind, target: f.target === "true", config, enabled: true, interval_minutes: 60 }));
    toast("Source ajoutée : elle sera lue au prochain passage"); viewSources();
  };
}

// ------------------------------------------------------------------ paramètres
function viewSettings() {
  const s = structuredClone(state.settings);
  const n = s.notifications || {}, th = s.thresholds || {}, mods = s.modifiers || {};
  const list = (arr) => (arr || []).join(", ");
  $("#view").innerHTML = `<h1>Paramètres</h1>
    <p class="muted small">Les changements s'appliquent au prochain passage du radar (les scores existants sont recalculés automatiquement).</p>
    <form id="set-form">
    <h2>Alertes</h2><div class="card pad"><div class="grid2">
      <label>Seuil « Prioritaire » (score ≥)<input type="number" name="th_priority" value="${th.priority ?? 60}" min="0" max="100"></label>
      <label>Seuil « Intéressante » (score ≥)<input type="number" name="th_interesting" value="${th.interesting ?? 35}" min="0" max="100"></label>
      <label>Alerte Telegram immédiate à partir de<input type="number" name="immediate" value="${n.immediate_min_score ?? 35}" min="0" max="100"></label>
      <label>Heures calmes (pas d'alerte) de … à …<div class="row"><input type="number" name="q0" value="${n.quiet_hours?.[0] ?? 23}" min="0" max="23" style="width:80px"> h → <input type="number" name="q1" value="${n.quiet_hours?.[1] ?? 7}" min="0" max="23" style="width:80px"> h</div></label>
      <label>Récap quotidien du matin<select name="digest"><option value="true" ${n.digest_enabled !== false ? "selected" : ""}>activé</option><option value="false" ${n.digest_enabled === false ? "selected" : ""}>désactivé</option></select></label>
      <label>Alerter aussi les CDI / CDD<select name="nonint"><option value="false" ${!n.notify_non_internships ? "selected" : ""}>non</option><option value="true" ${n.notify_non_internships ? "selected" : ""}>oui</option></select></label>
      <label>Candidature « sans réponse » après (jours)<input type="number" name="noans" value="${s.stats?.no_answer_after_days ?? 21}"></label>
    </div></div>
    <h2>Critères de score</h2>
    ${Object.entries(s.families || {}).map(([k, f]) => `<div class="card pad" style="margin-bottom:10px"><div class="grid2"><label>Famille<input name="fl_${k}" value="${esc(f.label)}"></label><label>Poids (points max)<input type="number" name="fw_${k}" value="${f.weight}"></label></div><label>Mots-clés (séparés par des virgules ; début de mot suffit : « manag » = management, manager)<textarea name="fk_${k}">${esc(list(f.keywords))}</textarea></label></div>`).join("")}
    <div class="card pad"><div class="grid2">
      ${[["internship_bonus", "Bonus stage"], ["target_company_bonus", "Bonus entreprise cible"], ["master_level_bonus", "Bonus niveau Master"], ["low_level_malus", "Malus niveau Bachelor/BTS"], ["recent_bonus", "Bonus publiée < 48 h"], ["off_target_malus", "Malus métier hors finance"], ["feedback_company_step", "Effet d'un avis 👍/👎"], ["feedback_company_max", "Effet max des avis"]].map(([k, l]) => `<label>${l}<input type="number" name="m_${k}" value="${mods[k] ?? 0}"></label>`).join("")}
    </div>
    <label>Entreprises cibles<textarea name="targets">${esc(list(s.target_companies))}</textarea></label>
    <label>Mots-clés hors cible (dans le titre)<textarea name="off">${esc(list(s.off_target_keywords))}</textarea></label>
    <label>Tes compétences (bonus si citées)<input name="skills" value="${esc(list(s.skills))}"></label></div>
    <h2>Statuts de candidature</h2>
    <div class="card pad"><label>Un statut par ligne : clé = libellé (les clés nouvelle, envoyee, entretien, offre, refus, abandonnee sont utilisées par les statistiques)</label>
      <textarea name="statuses" style="min-height:170px">${esc((s.statuses || []).map((x) => `${x.key} = ${x.label}`).join("\n"))}</textarea></div>
    <div class="actions"><button class="btn primary" type="submit">Enregistrer les paramètres</button></div></form>`;
  $("#set-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const split = (t) => t.split(",").map((x) => x.trim()).filter(Boolean);
    s.thresholds = { priority: +f.th_priority, interesting: +f.th_interesting };
    s.notifications = { ...n, immediate_min_score: +f.immediate, quiet_hours: [+f.q0, +f.q1], digest_enabled: f.digest === "true", notify_non_internships: f.nonint === "true" };
    s.stats = { ...(s.stats || {}), no_answer_after_days: +f.noans };
    for (const k of Object.keys(s.families || {})) s.families[k] = { label: f["fl_" + k], weight: +f["fw_" + k], keywords: split(f["fk_" + k]) };
    for (const k of Object.keys(f).filter((x) => x.startsWith("m_"))) s.modifiers[k.slice(2)] = +f[k];
    s.target_companies = split(f.targets); s.off_target_keywords = split(f.off); s.skills = split(f.skills);
    s.statuses = f.statuses.split("\n").map((l) => l.split("=")).filter((p) => p.length >= 2).map(([k, ...v]) => ({ key: k.trim(), label: v.join("=").trim() }));
    await must(sb.from("settings").upsert({ key: "profile", value: s, updated_at: new Date().toISOString() }));
    state.settings = s; state.statuses = s.statuses; toast("Paramètres enregistrés");
  };
}

boot();
