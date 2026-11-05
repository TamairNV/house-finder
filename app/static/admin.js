// Admin site: sites, setup wizard, logs, network and health. Plain JS, hash routing (#tab/arg).
const view = document.getElementById("view");
const fmtBytes = (n) => { n = Number(n || 0); const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return (i ? n.toFixed(n < 10 ? 1 : 0) : n) + " " + u[i]; };
const fmtTok = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n || 0));
const fmtCost = (c) => (c ? "$" + c.toFixed(c < 1 ? 3 : 2) : "–");
const fmtDur = (s) => { s = Math.round(s); if (s < 90) return s + "s"; const m = Math.round(s / 60); if (m < 90) return m + " min"; const h = Math.round(m / 60); return h < 48 ? h + " h" : Math.round(h / 24) + " days"; };
const num = (n) => Number(n || 0).toLocaleString("en-GB");
const yes = (b, on = "on", off = "off") => (b ? `<span class="ok">${on}</span>` : `<span class="err">${off}</span>`);
const stat = (v, label) => `<div class="panel stat"><b>${v}</b><span>${label}</span></div>`;
let SITES = [];
let timer = null;

function setTab(tab) {
  document.querySelectorAll("[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
}

async function route() {
  clearInterval(timer);
  const [tab, arg] = (location.hash.slice(1) || "overview").split("/");
  setTab(tab);
  try {
    if (tab === "sites" && arg) await siteDetail(arg);
    else await ({ overview, sites: sitesList, new: wizard, logs, network }[tab] || overview)(arg);
  } catch (e) {
    view.innerHTML = `<div class="empty panel">${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- overview

function siteStatus(s) {
  const u = s.usage || {};
  const steps = s.setup || {};
  const failed = Object.values(steps).some((x) => x && x.ok === false);
  const waiting = Object.values(steps).some((x) => x && x.ok === null);
  if (!s.enabled) return `<span class="chip">off</span>`;
  if (failed) return `<span class="chip bad">setup needs a look</span>`;
  if (waiting) return `<span class="chip warn">setting up…</span>`;
  if (u.scraping) return `<span class="chip accent"><span class="pulse"></span>scraping</span>`;
  if (u.failed_runs_24h) return `<span class="chip warn">${u.failed_runs_24h} failed run${u.failed_runs_24h > 1 ? "s" : ""}</span>`;
  return `<span class="chip good">ok</span>`;
}

function siteCard(s) {
  const u = s.usage || {};
  return `<a class="panel site-card" href="#sites/${esc(s.slug)}">
    <div class="site-head"><b>${esc(s.name)}</b>${siteStatus(s)}</div>
    <div class="muted small">${esc(s.hostname)}${s.is_default ? " · original" : ""}</div>
    <div class="site-nums">
      <div><b>${num(u.passing)}</b><span>fit</span></div>
      <div><b>${num(u.new_24h)}</b><span>new today</span></div>
      <div><b>${num(u.listings)}</b><span>listings</span></div>
      <div><b>${num(u.viewers_7d)}</b><span>people this week</span></div>
    </div>
    <div class="muted small">Last scrape ${ago(u.last_scrape_ok)} · ${num(u.requests_7d)} requests this week · AI ${num(u.ai_requests_30d)} (${fmtCost(u.ai_cost_30d)}) · ${num(u.emails_30d)} emails</div>
  </a>`;
}

async function overview() {
  clearInterval(timer);
  const d = await api("/api/overview");
  SITES = d.sites;
  const h = d.health;
  const diskPct = Math.round((h.disk.used / h.disk.total) * 100);
  view.innerHTML = `
    <div class="toolbar"><h2 style="margin:0">Overview</h2><div class="spacer"></div><a class="btn primary-btn" href="#new">➕ New site</a></div>
    <div class="stat-row">
      ${stat(d.sites.length, "sites")}
      ${stat(num(d.sites.reduce((a, s) => a + ((s.usage || {}).passing || 0), 0)), "places that fit, all sites")}
      ${stat(num(d.sites.reduce((a, s) => a + ((s.usage || {}).viewers_7d || 0), 0)), "people logged in this week")}
      ${stat(fmtDur(h.uptime_s), "app uptime")}
    </div>
    <h3 class="sec">Sites</h3>
    <div class="site-grid">${d.sites.map(siteCard).join("")}</div>
    <h3 class="sec">Health</h3>
    <div class="settings-grid">
      <div class="panel"><div class="kv">
        <div>Version</div><div>${esc(h.version || "?")} · Python ${esc(h.python)}</div>
        <div>Scheduler</div><div>${yes(h.scheduler.running, "running", "off")}${h.scheduler.scraping.length ? ` · scraping ${esc(h.scheduler.scraping.join(", "))}` : ""}</div>
        <div>Next check</div><div>${h.scheduler.jobs.scrape ? fmtDateTime(h.scheduler.jobs.scrape) : "–"}</div>
        <div>Email (SMTP)</div><div>${yes(h.smtp, "configured", "not set up")}</div>
        <div>AI</div><div>${h.ai ? `<span class="ok">${esc(h.ai)}</span>` : '<span class="err">off</span>'}</div>
        <div>Cloudflare token</div><div>${yes(h.cloudflare_token, "set", "missing (setup button can't do DNS/login)")}</div>
        <div>Routing API</div><div>${h.routing_api ? "openrouteservice" : "estimates only"}</div>
      </div></div>
      <div class="panel"><div class="kv">
        <div>Disk</div><div><div class="bar" title="${diskPct}%"><span style="width:${diskPct}%"></span></div>${fmtBytes(h.disk.free)} free of ${fmtBytes(h.disk.total)}</div>
        <div>App data</div><div>${fmtBytes(h.data_dir_bytes)} (photos ${fmtBytes(h.photos_bytes)})</div>
        <div>Memory</div><div>${fmtBytes(h.memory_rss)}</div>
        <div>Load</div><div>${h.load.join(" · ")} on ${h.cpus} CPUs</div>
        <div>Host</div><div>${esc(h.host)}</div>
      </div></div>
    </div>`;
  timer = setInterval(() => { if (location.hash.startsWith("#overview") || !location.hash) overview(); }, 30000);
}

// ---------------------------------------------------------------- sites

async function sitesList() {
  SITES = await api("/api/sites");
  view.innerHTML = `
    <div class="toolbar"><h2 style="margin:0">Sites</h2><div class="spacer"></div><a class="btn primary-btn" href="#new">➕ New site</a></div>
    <div class="table-wrap"><table class="list"><thead><tr><th>Site</th><th>Address</th><th>Status</th><th>Fit / all</th><th>Last scrape</th><th>Visits 7d</th><th>AI 30d</th><th>Emails 30d</th></tr></thead><tbody>
    ${SITES.map((s) => { const u = s.usage || {}; return `<tr class="clickable" onclick="location.hash='#sites/${esc(s.slug)}'">
      <td><b>${esc(s.name)}</b><div class="muted small">${esc(s.slug)}</div></td>
      <td><a href="${esc(s.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(s.hostname)}</a></td>
      <td>${siteStatus(s)}</td><td>${num(u.passing)} / ${num(u.listings)}</td><td>${ago(u.last_scrape_ok)}</td>
      <td>${num(u.requests_7d)} · ${num(u.viewers_7d)} people</td><td>${num(u.ai_requests_30d)} · ${fmtCost(u.ai_cost_30d)}</td>
      <td>${num(u.emails_30d)}${u.email_failures_30d ? ` <span class="err">(${u.email_failures_30d} failed)</span>` : ""}</td></tr>`; }).join("")}
    </tbody></table></div>`;
}

function stepRow(step, st) {
  const icon = !st || st.ok === null ? (st && st.msg === "waiting" ? "○" : "<span class='spin'></span>") : st.ok ? "✅" : "⚠️";
  return `<div class="step"><span class="step-icon">${icon}</span><div><b>${esc(step.label)}</b><div class="muted small">${esc(st ? st.msg || "" : "")}${st && st.at ? ` · ${ago(st.at)}` : ""}</div></div></div>`;
}

async function siteDetail(slug) {
  const s = await api(`/api/sites/${encodeURIComponent(slug)}`);
  const u = s.usage || {};
  const busy = Object.values(s.setup || {}).some((x) => x && x.ok === null && x.msg !== "waiting");
  view.innerHTML = `
    <div class="toolbar"><a href="#sites" class="btn">←</a><h2 style="margin:0">${esc(s.name)}</h2>${siteStatus(s)}<div class="spacer"></div>
      <a class="btn" href="${esc(s.url)}" target="_blank" rel="noopener">Open site ↗</a>
      <button id="scrape">Scrape now</button><button id="check">Check it's live</button></div>
    <div class="stat-row">
      ${stat(num(u.passing), "places that fit")}${stat(num(u.listings), "active listings")}
      ${stat(num(u.viewers_7d), "people this week")}${stat(fmtBytes(u.db_bytes), "database")}
    </div>
    <div class="settings-grid" style="margin-top:16px">
      <div class="panel"><h3>Setup</h3>
        ${s.is_default ? `<p class="muted">The original site. Its DNS, tunnel and login were set up by hand.</p>` :
          `${s.steps.map((st) => stepRow(st, s.setup[st.key])).join("")}
           <div style="margin-top:10px"><button id="retry" ${busy ? "disabled" : ""}>Run setup again</button>
           <span class="muted small">Safe to repeat: finished steps are skipped.</span></div>`}
      </div>
      <div class="panel"><h3>Details</h3>
        <div class="form-row"><label>Name</label><input id="f-name" value="${esc(s.name)}"></div>
        <div class="form-row"><label>Address</label><div><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.hostname)}</a></div></div>
        ${s.is_default ? "" : `<div class="form-row"><label>Site on</label><input type="checkbox" id="f-enabled" ${s.enabled ? "checked" : ""}></div>
        <div class="form-row"><label>Login required</label><input type="checkbox" id="f-strict" ${s.strict_auth ? "checked" : ""}><div class="hint">Off only for debugging: anyone reaching the app directly could see it.</div></div>`}
        <div class="form-row"><label>Owners</label><input id="f-owners" value="${esc(s.admin_emails)}" placeholder="email, email"><div class="hint">Can change who logs in from the site's own Settings.</div></div>
        <div class="form-row"><label>Notes</label><textarea id="f-notes">${esc(s.notes || "")}</textarea></div>
        <button class="primary" id="save">Save</button>
      </div>
      <div class="panel"><h3>Who can log in</h3><div id="access"><p class="muted">Loading…</p></div></div>
      <div class="panel"><h3>Activity</h3><div class="kv">
        <div>Search</div><div>${esc(u.profile_name || "")} · ${u.group_size || "?"} people · £${u.budget_pppw || "?"}pppw</div>
        <div>People</div><div>${esc((u.people || []).join(", ") || "–")}</div>
        <div>Last scrape</div><div>${ago(u.last_scrape_ok)}${u.last_run ? ` (last run: ${esc(u.last_run.source)} ${u.last_run.ok ? "ok" : "failed"})` : ""}</div>
        <div>Failed runs (24h)</div><div>${num(u.failed_runs_24h)}</div>
        <div>Digest recipients</div><div>${num(u.recipients)}</div>
        <div>Emails (30d)</div><div>${num(u.emails_30d)}${u.email_failures_30d ? ` · <span class="err">${u.email_failures_30d} failed</span>` : ""}</div>
        <div>AI (30d)</div><div>${num(u.ai_requests_30d)} requests · ${fmtCost(u.ai_cost_30d)}</div>
        <div>Last visit</div><div>${ago(u.last_visit)}</div>
        <div>Created</div><div>${fmtDateTime(s.created_at)}</div>
      </div>
      <p><a href="#logs/scrapes?site=${esc(s.slug)}">Scrape log</a> · <a href="#logs/ai?site=${esc(s.slug)}">AI log</a> · <a href="#logs/emails?site=${esc(s.slug)}">Email log</a> · <a href="#logs/app?site=${esc(s.slug)}">App log</a></p></div>
      ${s.is_default ? "" : `<div class="panel danger-zone"><h3>Delete site</h3><p class="muted">Removes its login, tunnel route and DNS record, and moves its database to <code>data/sites/_deleted/</code> (nothing is wiped).</p><button class="danger" id="delete">Delete ${esc(s.slug)}…</button></div>`}
    </div>`;

  const $ = (id) => document.getElementById(id);
  $("scrape").onclick = async () => toast((await api(`/api/sites/${slug}/scrape`, { method: "POST" })).message);
  $("check").onclick = async () => { const r = await api(`/api/sites/${slug}/check`, { method: "POST" }); toast((r.ok ? "✅ " : "⚠️ ") + r.msg, 5000); };
  if ($("retry")) $("retry").onclick = async () => { await api(`/api/sites/${slug}/setup`, { method: "POST", body: {} }); toast("Setting up…"); setTimeout(() => siteDetail(slug), 800); };
  $("save").onclick = async () => {
    const body = { name: $("f-name").value, admin_emails: $("f-owners").value, notes: $("f-notes").value };
    if ($("f-enabled")) { body.enabled = $("f-enabled").checked; body.strict_auth = $("f-strict").checked; }
    await api(`/api/sites/${slug}`, { method: "PATCH", body }); toast("Saved"); siteDetail(slug);
  };
  if ($("delete")) $("delete").onclick = async () => {
    const typed = prompt(`This takes ${s.hostname} offline. Type "${slug}" to confirm.`);
    if (typed !== slug) return;
    const r = await api(`/api/sites/${slug}?confirm=${encodeURIComponent(typed)}`, { method: "DELETE" });
    alert("Done:\n" + r.done.join("\n")); location.hash = "#sites";
  };
  loadAccess(slug);
  if (busy || Object.values(s.setup || {}).some((x) => x && x.ok === null)) timer = setInterval(async () => {
    if (location.hash !== `#sites/${slug}`) return clearInterval(timer);
    const n = await api(`/api/sites/${slug}`);
    if (JSON.stringify(n.setup) !== JSON.stringify(s.setup)) { clearInterval(timer); siteDetail(slug); }
  }, 2500);
}

async function loadAccess(slug) {
  const box = document.getElementById("access");
  const a = await api(`/api/sites/${slug}/access`);
  if (!a.configured) { box.innerHTML = `<p class="muted">Needs HF_CF_API_TOKEN in .env.</p>`; return; }
  if (a.error) { box.innerHTML = `<p class="err">${esc(a.error)}</p>`; return; }
  if (a.read_only) { box.innerHTML = `${a.emails.map((e) => `<div class="access-row"><span>${esc(e)}</span></div>`).join("")}<p class="muted small">Managed by the access panel.</p>`; return; }
  box.innerHTML = `${a.emails.map((e) => `<div class="access-row"><span>${esc(e)}${e === window.ME ? ' <span class="muted">(you)</span>' : ""}</span>
      ${e === window.ME ? "" : `<button class="small danger" data-rm="${esc(e)}">Remove</button>`}</div>`).join("")}
    ${a.other_rules ? `<p class="muted small">+ ${a.other_rules} other rule(s) in Cloudflare.</p>` : ""}
    <div class="ask" style="max-width:480px"><input id="new-email" type="email" placeholder="friend@gmail.com"><button class="primary" id="add-email">Add</button></div>
    <p class="muted small">They log in with a one-time code sent to that email (policy: ${esc(a.policy || "")}).</p>`;
  box.querySelectorAll("[data-rm]").forEach((b) => b.onclick = async () => {
    if (!confirm(`Stop ${b.dataset.rm} logging in?`)) return;
    try { await api(`/api/sites/${slug}/access/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }); loadAccess(slug); } catch (e) { toast(e.message); }
  });
  document.getElementById("add-email").onclick = async () => {
    try { await api(`/api/sites/${slug}/access`, { method: "POST", body: { email: document.getElementById("new-email").value } }); loadAccess(slug); } catch (e) { toast(e.message); }
  };
}

// ---------------------------------------------------------------- new site wizard

const W = { step: 1, name: "", slug: "", people: [{ email: "", name: "", digest: true, owner: true }], copy_from: "", group_size: "", budget_pppw: "", include_admins: true, digest_hour: 18, notes: "" };
const slugify = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32);

async function wizard() {
  if (!SITES.length) SITES = await api("/api/sites");
  const steps = ["Name", "People", "Search", "Check & create"];
  view.innerHTML = `
    <div class="toolbar"><h2 style="margin:0">New site</h2></div>
    <div class="wiz-steps">${steps.map((t, i) => `<span class="${i + 1 === W.step ? "on" : i + 1 < W.step ? "done" : ""}">${i + 1}. ${t}</span>`).join("")}</div>
    <div class="panel wiz" id="wiz"></div>`;
  const box = document.getElementById("wiz");
  const nav = (back, next, label = "Next") => `<div class="wiz-nav">${back ? `<button id="back">Back</button>` : ""}<div class="spacer"></div><button class="primary" id="next">${label}</button></div>`;

  if (W.step === 1) {
    box.innerHTML = `<h3>What's the site called?</h3>
      <div class="form-row"><label>Name</label><input id="w-name" value="${esc(W.name)}" placeholder="Zach's house hunt"></div>
      <div class="form-row"><label>Web address</label><div class="slug-row"><input id="w-slug" value="${esc(W.slug)}" placeholder="zach"><span class="muted">.${esc(window.DOMAIN)}</span></div>
        <div class="hint" id="slug-hint">Lowercase letters, numbers and dashes.</div></div>
      ${nav(false, true)}`;
    const name = document.getElementById("w-name"), slug = document.getElementById("w-slug"), hint = document.getElementById("slug-hint");
    let touched = !!W.slug;
    const check = async () => {
      if (!slug.value) { hint.textContent = "Lowercase letters, numbers and dashes."; return false; }
      const r = await api("/api/check-slug?slug=" + encodeURIComponent(slug.value));
      hint.innerHTML = r.ok ? `<span class="ok">✓ ${esc(r.hostname)} is free</span>` : `<span class="err">${esc(r.problem)}</span>`;
      return r.ok;
    };
    name.oninput = () => { if (!touched) { slug.value = slugify(name.value.replace(/'s\b.*$/i, "")); check(); } };
    slug.oninput = () => { touched = true; slug.value = slugify(slug.value); check(); };
    if (slug.value) check();
    document.getElementById("next").onclick = async () => {
      W.name = name.value.trim(); W.slug = slug.value;
      if (!W.name) return toast("Give it a name");
      if (!(await check())) return toast("Pick another web address");
      W.step = 2; wizard();
    };
  } else if (W.step === 2) {
    box.innerHTML = `<h3>Who's in the group?</h3>
      <p class="muted">Everyone here can log in with their email. Tick "daily email" for the 6pm digest, and "owner" for people who can add friends themselves.</p>
      <div id="people">${W.people.map((p, i) => `<div class="person-row" data-i="${i}">
        <input data-k="email" type="email" value="${esc(p.email)}" placeholder="email@example.com">
        <input data-k="name" value="${esc(p.name)}" placeholder="Name">
        <label class="inline"><input type="checkbox" data-k="digest" ${p.digest ? "checked" : ""}>daily email</label>
        <label class="inline"><input type="checkbox" data-k="owner" ${p.owner ? "checked" : ""}>owner</label>
        <button class="small danger" data-rm="${i}">✕</button></div>`).join("")}</div>
      <button id="add-person">+ Add someone</button>
      <div class="form-row" style="margin-top:14px"><label>Me too</label><label class="inline"><input type="checkbox" id="w-admins" ${W.include_admins ? "checked" : ""}>Let me (admin) log in as well</label></div>
      <div class="form-row"><label>Digest time</label><select id="w-hour">${Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === W.digest_hour ? "selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("")}</select></div>
      ${nav(true, true)}`;
    const read = () => {
      box.querySelectorAll(".person-row").forEach((row) => {
        const p = W.people[row.dataset.i];
        row.querySelectorAll("[data-k]").forEach((el) => { p[el.dataset.k] = el.type === "checkbox" ? el.checked : el.value.trim(); });
      });
      W.include_admins = document.getElementById("w-admins").checked;
      W.digest_hour = Number(document.getElementById("w-hour").value);
    };
    document.getElementById("add-person").onclick = () => { read(); W.people.push({ email: "", name: "", digest: true, owner: false }); wizard(); };
    box.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => { read(); W.people.splice(Number(b.dataset.rm), 1); wizard(); });
    document.getElementById("back").onclick = () => { read(); W.step = 1; wizard(); };
    document.getElementById("next").onclick = () => {
      read();
      W.people = W.people.filter((p) => p.email);
      if (!W.people.length && !W.include_admins) return toast("Add at least one email");
      const bad = W.people.find((p) => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(p.email));
      if (bad) return toast(`${bad.email} isn't an email`);
      if (!W.people.length) W.people = [];
      W.step = 3; wizard();
    };
  } else if (W.step === 3) {
    box.innerHTML = `<h3>What are they looking for?</h3>
      <p class="muted">Start from an existing site's settings (areas, sources, bills, scoring) or the defaults. Everything can be changed later in the site's own Settings.</p>
      <div class="form-row"><label>Start from</label><select id="w-copy"><option value="">Defaults (Newcastle, 5 people, £130pppw)</option>
        ${SITES.map((s) => `<option value="${esc(s.slug)}" ${W.copy_from === s.slug ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></div>
      <div class="form-row"><label>Group size</label><input id="w-size" type="number" min="1" max="12" value="${esc(W.group_size)}" placeholder="same as above"></div>
      <div class="form-row"><label>Budget (£pppw incl. bills)</label><input id="w-budget" type="number" min="40" max="500" value="${esc(W.budget_pppw)}" placeholder="same as above"></div>
      <div class="form-row"><label>Notes</label><textarea id="w-notes" placeholder="Only for you">${esc(W.notes)}</textarea></div>
      <p class="muted small">The scrapers are set up for Newcastle. Another city needs its search names changing in Settings → Search area.</p>
      ${nav(true, true)}`;
    const read = () => { W.copy_from = document.getElementById("w-copy").value; W.group_size = document.getElementById("w-size").value; W.budget_pppw = document.getElementById("w-budget").value; W.notes = document.getElementById("w-notes").value; };
    document.getElementById("back").onclick = () => { read(); W.step = 2; wizard(); };
    document.getElementById("next").onclick = () => { read(); W.step = 4; wizard(); };
  } else {
    const copy = SITES.find((s) => s.slug === W.copy_from);
    box.innerHTML = `<h3>Ready?</h3>
      <div class="kv">
        <div>Name</div><div>${esc(W.name)}</div>
        <div>Address</div><div><b>${esc(W.slug)}.${esc(window.DOMAIN)}</b></div>
        <div>People</div><div>${W.people.map((p) => `${esc(p.name || p.email)}${p.digest ? " 📧" : ""}${p.owner ? " 🔑" : ""}`).join(", ") || "–"}${W.include_admins ? " + you" : ""}</div>
        <div>Search</div><div>${copy ? "Copied from " + esc(copy.name) : "Defaults"}${W.group_size ? ` · ${esc(W.group_size)} people` : ""}${W.budget_pppw ? ` · £${esc(W.budget_pppw)}pppw` : ""}</div>
      </div>
      <p class="muted">Pressing create makes the database, then (through Cloudflare) adds the DNS record, routes the tunnel and sets up the email login. The first scrape starts within a few minutes.</p>
      ${nav(true, true, "🚀 Create site")}`;
    document.getElementById("back").onclick = () => { W.step = 3; wizard(); };
    document.getElementById("next").onclick = async (ev) => {
      ev.target.disabled = true;
      try {
        const body = { name: W.name, slug: W.slug, people: W.people.map((p) => ({ ...p, name: p.name || null })), copy_from: W.copy_from || null,
          group_size: W.group_size ? Number(W.group_size) : null, budget_pppw: W.budget_pppw ? Number(W.budget_pppw) : null,
          include_admins: W.include_admins, digest_hour: W.digest_hour, notes: W.notes || null };
        const s = await api("/api/sites", { method: "POST", body });
        Object.assign(W, { step: 1, name: "", slug: "", people: [{ email: "", name: "", digest: true, owner: true }], copy_from: "", group_size: "", budget_pppw: "", notes: "" });
        location.hash = "#sites/" + s.slug;
      } catch (e) { ev.target.disabled = false; toast(e.message, 5000); }
    };
  }
}

// ---------------------------------------------------------------- logs

const who = (u) => (u ? esc(u) : `<span class="muted">home network</span>`);
const siteOpts = (sel) => `<option value="">All sites</option>${SITES.map((s) => `<option value="${esc(s.slug)}" ${s.slug === sel ? "selected" : ""}>${esc(s.name)}</option>`).join("")}`;

async function logs(arg) {
  if (!SITES.length) SITES = await api("/api/sites");
  const [kind0, qs] = (arg || "ai").split("?");
  const kind = kind0 || "ai";
  const params = new URLSearchParams(qs || "");
  const site = params.get("site") || "";
  const go = (k, extra = {}) => { const p = new URLSearchParams({ site, ...extra }); [...p.keys()].forEach((x) => !p.get(x) && p.delete(x)); location.hash = `#logs/${k}${p.toString() ? "?" + p : ""}`; };
  view.innerHTML = `
    <div class="toolbar"><h2 style="margin:0">Logs</h2>
      <div class="seg">${[["ai", "AI"], ["scrapes", "Scrapes"], ["emails", "Emails"], ["app", "App"]].map(([k, t]) => `<button data-k="${k}" class="${k === kind ? "on" : ""}">${t}</button>`).join("")}</div>
      <div class="spacer"></div><select id="site">${siteOpts(site)}</select><span id="extra"></span></div>
    <div id="log-body"><p class="muted">Loading…</p></div>`;
  view.querySelectorAll(".seg button").forEach((b) => b.onclick = () => go(b.dataset.k));
  document.getElementById("site").onchange = (e) => { const p = new URLSearchParams(qs || ""); p.set("site", e.target.value); if (!e.target.value) p.delete("site"); location.hash = `#logs/${kind}${p.toString() ? "?" + p : ""}`; };
  const body = document.getElementById("log-body");
  const extra = document.getElementById("extra");
  const q = site ? `site=${encodeURIComponent(site)}` : "";

  if (kind === "ai") {
    const days = params.get("days") || "30";
    extra.innerHTML = `<select id="days">${[["1", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["365", "Last year"]].map(([v, t]) => `<option value="${v}" ${v === days ? "selected" : ""}>${t}</option>`).join("")}</select>`;
    document.getElementById("days").onchange = (e) => go("ai", { days: e.target.value });
    const d = await api(`/api/logs/ai?days=${days}&${q}`);
    const tot = d.people.reduce((a, p) => ({ n: a.n + p.requests, tin: a.tin + p.input_tokens, tout: a.tout + p.output_tokens, cost: a.cost + p.cost_usd }), { n: 0, tin: 0, tout: 0, cost: 0 });
    body.innerHTML = `
      <div class="stat-row">${stat(tot.n, "requests")}${stat(new Set(d.people.map((p) => p.user)).size, "people")}${stat(fmtTok(tot.tin + tot.tout), "tokens")}${stat(fmtCost(tot.cost), "API-equivalent")}</div>
      <p class="muted small" style="margin:6px 2px 14px">Runs on your Claude subscription, so the cost is what it would be on the pay-as-you-go API, not what you're charged.</p>
      <div class="panel"><h3>By person</h3>${d.people.length ? `<div class="table-wrap"><table class="list"><thead><tr><th>Who</th><th>Site</th><th>Requests</th><th>Tokens in / out</th><th>Cost</th><th>Last used</th></tr></thead><tbody>
        ${d.people.map((p) => `<tr><td>${who(p.user)}</td><td>${esc(p.site)}</td><td>${p.requests}${p.failed ? ` <span class="muted">(${p.failed} failed)</span>` : ""}</td>
          <td>${fmtTok(p.input_tokens)} / ${fmtTok(p.output_tokens)}</td><td>${fmtCost(p.cost_usd)}</td><td>${ago(p.last)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">Nobody has used the AI in this period.</p>`}</div>
      <div class="panel" style="margin-top:16px"><h3>Every request</h3>${d.rows.length ? `<div class="log-list">${d.rows.map((r) => `<div class="log-row${r.ok ? "" : " failed"}">
          <div class="log-top"><span>${who(r.user)} <span class="chip">${esc(r.site)}</span></span><span class="muted">${ago(r.at)}</span></div>
          <div>${r.kind === "overview" ? "✨ Overview" : "💬 " + esc(r.question || "Question")}${r.listing_id ? ` · <a href="${esc(r.site_url)}/listing/${r.listing_id}" target="_blank" rel="noopener">${esc(r.title || "listing #" + r.listing_id)}</a>` : ""}</div>
          <div class="muted log-meta">${r.ok ? "" : `<span class="err">Failed: ${esc(r.error || "")}</span> · `}${fmtTok(r.input_tokens)} in / ${fmtTok(r.output_tokens)} out · ${fmtCost(r.cost_usd)}${r.duration_ms ? ` · ${(r.duration_ms / 1000).toFixed(1)}s` : ""}${r.model ? ` · ${esc(r.model)}` : ""}</div></div>`).join("")}</div>` : `<p class="muted">No requests yet.</p>`}</div>`;
  } else if (kind === "scrapes") {
    const rows = await api(`/api/logs/scrapes?${q}`);
    body.innerHTML = `<div class="table-wrap"><table class="list"><thead><tr><th>Started</th><th>Site</th><th>Source</th><th>Result</th><th>Requests</th><th>Found</th><th>New</th><th>Updated</th><th>Took</th><th>Error</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${fmtDateTime(r.started_at)}</td><td>${esc(r.site)}</td><td>${SOURCE_LABEL[r.source] || esc(r.source)}</td>
        <td>${r.finished_at ? (r.ok ? '<span class="ok">ok</span>' : '<span class="err">failed</span>') : "running"}</td>
        <td>${r.requests}</td><td>${r.found}</td><td>${r.new}</td><td>${r.updated}</td>
        <td>${r.finished_at ? fmtDur((new Date(r.finished_at + "Z") - new Date(r.started_at + "Z")) / 1000) : ""}</td>
        <td>${r.error ? `<details><summary class="err">error</summary><pre class="err">${esc(r.error)}</pre></details>` : ""}</td></tr>`).join("") || `<tr><td colspan="10" class="muted">No runs yet.</td></tr>`}
      </tbody></table></div>`;
  } else if (kind === "emails") {
    const rows = await api(`/api/logs/emails?${q}`);
    body.innerHTML = `<div class="table-wrap"><table class="list"><thead><tr><th>Sent</th><th>Site</th><th>Type</th><th>Subject</th><th>Listings</th><th>Result</th></tr></thead><tbody>
      ${rows.map((e) => `<tr><td>${fmtDateTime(e.sent_at)}</td><td>${esc(e.site)}</td><td>${esc(e.kind)}</td><td>${esc(e.subject)}</td><td>${e.count}</td>
        <td>${e.ok ? '<span class="ok">sent</span>' : `<span class="err">failed</span><div class="muted small">${esc(e.error || "")}</div>`}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No emails yet.</td></tr>`}
      </tbody></table></div>`;
  } else {
    const level = params.get("level") || "INFO", text = params.get("q") || "";
    extra.innerHTML = `<select id="level">${["INFO", "WARNING", "ERROR"].map((l) => `<option ${l === level ? "selected" : ""}>${l}</option>`).join("")}</select>
      <input id="q" placeholder="Search" value="${esc(text)}" style="width:140px">`;
    document.getElementById("level").onchange = (e) => go("app", { level: e.target.value, q: text });
    document.getElementById("q").onchange = (e) => go("app", { level, q: e.target.value });
    const rows = await api(`/api/logs/app?level=${level}&q=${encodeURIComponent(text)}&${q}`);
    body.innerHTML = `<div class="panel applog">${rows.map((r) => `<div class="logline lv-${r.level.toLowerCase()}"><span class="muted">${fmtDateTime(r.at)}</span> <b>${r.level}</b> ${r.site ? `<span class="chip">${esc(r.site)}</span>` : ""} <span class="muted">${esc(r.logger)}</span><pre>${esc(r.msg)}</pre></div>`).join("") || `<p class="muted">Nothing logged since the app started (the full file is data/logs/app.log).</p>`}</div>`;
  }
}

// ---------------------------------------------------------------- network

function bars(series, key, label, fmt = num) {
  if (!series.length) return `<p class="muted">No data yet.</p>`;
  const max = Math.max(1, ...series.map((s) => s[key]));
  const w = 100 / series.length;
  return `<div class="chart" role="img" aria-label="${esc(label)}"><svg viewBox="0 0 100 40" preserveAspectRatio="none">
    ${series.map((s, i) => { const h = (s[key] / max) * 38; return `<rect x="${i * w + w * 0.12}" y="${40 - h}" width="${w * 0.76}" height="${h}" rx="0.4"><title>${esc(s.t)}: ${fmt(s[key])}</title></rect>`; }).join("")}
  </svg><div class="chart-axis muted small"><span>${esc(series[0].t)}</span><span>peak ${fmt(max)}</span><span>${esc(series[series.length - 1].t)}</span></div></div>`;
}

async function network(arg) {
  clearInterval(timer);
  const hours = Number((arg || "").replace(/\D/g, "")) || 24;
  const d = await api(`/api/network?hours=${hours}`);
  const i = d.inbound, o = d.outbound;
  view.innerHTML = `
    <div class="toolbar"><h2 style="margin:0">Network</h2><div class="spacer"></div>
      <select id="hours">${[[1, "Last hour"], [24, "Last 24 hours"], [168, "Last 7 days"], [720, "Last 30 days"]].map(([v, t]) => `<option value="${v}" ${v === hours ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <h3 class="sec">Visitors (requests to our sites)</h3>
    <div class="stat-row">${stat(num(i.requests), "requests")}${stat(fmtBytes(i.bytes), "sent")}${stat(num(i.viewers), "people")}${stat(`${i.p50_ms ?? "–"} / ${i.p95_ms ?? "–"} ms`, "response p50 / p95")}</div>
    <div class="panel" style="margin-top:12px"><h3>Requests per ${d.bucket}</h3>${bars(i.series, "requests", "Requests over time")}</div>
    <div class="settings-grid" style="margin-top:16px">
      <div class="panel"><h3>By site</h3><table class="list"><thead><tr><th>Site</th><th>Requests</th><th>Sent</th><th>Avg</th><th>People</th></tr></thead><tbody>
        ${i.by_site.map((s) => `<tr><td>${esc(s.site)}</td><td>${num(s.requests)}</td><td>${fmtBytes(s.bytes)}</td><td>${s.avg_ms} ms</td><td>${s.viewers}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">Nothing yet.</td></tr>`}</tbody></table></div>
      <div class="panel"><h3>Responses</h3><div class="kv">
        ${Object.entries(i.statuses).map(([k, v]) => `<div>${k[0]}xx</div><div>${num(v)}</div>`).join("")}
        <div>Via Cloudflare</div><div>${num(i.via.cloudflare || 0)}</div><div>Direct (LAN/Tailscale)</div><div>${num(i.via.direct || 0)}</div>
        <div>Countries</div><div>${i.countries.map((c) => `${esc(c.country)} ${num(c.requests)}`).join(" · ") || "–"}</div></div></div>
      <div class="panel"><h3>Who</h3><table class="list"><thead><tr><th>Person</th><th>Site</th><th>Requests</th><th>Last seen</th></tr></thead><tbody>
        ${i.viewers_list.map((v) => `<tr><td>${esc(v.viewer)}</td><td>${esc(v.site)}</td><td>${num(v.requests)}</td><td>${ago(v.last)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No logged-in visits yet.</td></tr>`}</tbody></table></div>
      <div class="panel"><h3>Busiest pages</h3><table class="list"><thead><tr><th>Site</th><th>Path</th><th>Requests</th><th>Avg</th></tr></thead><tbody>
        ${i.top_paths.map((p) => `<tr><td>${esc(p.site)}</td><td><code>${esc(p.path)}</code></td><td>${num(p.requests)}</td><td>${p.avg_ms} ms</td></tr>`).join("")}</tbody></table></div>
      <div class="panel"><h3>Errors</h3>${i.errors.length ? `<table class="list"><thead><tr><th>When</th><th>Site</th><th>Request</th><th>Status</th></tr></thead><tbody>
        ${i.errors.map((e) => `<tr><td>${ago(e.at)}</td><td>${esc(e.site)}</td><td><code>${esc(e.method)} ${esc(e.path)}</code></td><td class="${e.status >= 500 ? "err" : ""}">${e.status}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">No errors 🎉</p>`}</div>
      <div class="panel"><h3>Slowest</h3><table class="list"><thead><tr><th>When</th><th>Request</th><th>Time</th></tr></thead><tbody>
        ${i.slowest.map((e) => `<tr><td>${ago(e.at)}</td><td><code>${esc(e.site)} ${esc(e.method)} ${esc(e.path)}</code></td><td>${num(e.ms)} ms</td></tr>`).join("")}</tbody></table></div>
    </div>
    <h3 class="sec">Scrapers (requests to listing sites)</h3>
    <div class="stat-row">${stat(num(o.requests), "requests")}${stat(fmtBytes(o.bytes), "downloaded")}${stat(`${o.avg_ms} ms`, "average")}${stat(num(o.hosts.reduce((a, h) => a + h.rate_limited + h.blocked, 0)), "rate-limited / blocked")}</div>
    <div class="panel" style="margin-top:12px"><h3>Requests per ${d.bucket}</h3>${bars(o.series, "requests", "Scraper requests over time")}</div>
    <div class="panel" style="margin-top:16px"><h3>By listing site</h3><div class="table-wrap"><table class="list"><thead><tr><th>Host</th><th>Scraper</th><th>Requests</th><th>Downloaded</th><th>Avg</th><th>Network errors</th><th>429/503</th><th>403</th><th>Other errors</th></tr></thead><tbody>
      ${o.hosts.map((h) => `<tr><td>${esc(h.host)}</td><td>${SOURCE_LABEL[h.source] || esc(h.source)}</td><td>${num(h.requests)}</td><td>${fmtBytes(h.bytes)}</td><td>${h.avg_ms} ms</td>
        <td class="${h.network_errors ? "err" : ""}">${h.network_errors}</td><td class="${h.rate_limited ? "err" : ""}">${h.rate_limited}</td><td class="${h.blocked ? "err" : ""}">${h.blocked}</td><td>${h.other_errors}</td></tr>`).join("") || `<tr><td colspan="9" class="muted">No scrapes in this period.</td></tr>`}</tbody></table></div>
      ${o.by_site.length > 1 ? `<p class="muted small">By site: ${o.by_site.map((s) => `${esc(s.site)} ${num(s.requests)} (${fmtBytes(s.bytes)})`).join(" · ")}</p>` : ""}</div>
    <h3 class="sec">Network interfaces</h3>
    <div class="panel"><table class="list"><thead><tr><th>Interface</th><th>Received</th><th>Sent</th><th>Now in</th><th>Now out</th></tr></thead><tbody>
      ${d.interfaces.map((n) => `<tr><td>${esc(n.name)}</td><td>${fmtBytes(n.rx_bytes)}</td><td>${fmtBytes(n.tx_bytes)}</td><td>${n.rx_rate == null ? "…" : fmtBytes(n.rx_rate) + "/s"}</td><td>${n.tx_rate == null ? "…" : fmtBytes(n.tx_rate) + "/s"}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">Not available here.</td></tr>`}</tbody></table>
      <p class="muted small">Totals since the container started. Live rates fill in on the next refresh (every 15 s).</p></div>`;
  document.getElementById("hours").onchange = (e) => (location.hash = "#network/" + e.target.value);
  timer = setInterval(() => { if (location.hash.startsWith("#network")) network(arg); else clearInterval(timer); }, 15000);
}

route();
