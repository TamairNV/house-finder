// Settings page: edits the profile JSON and the email recipients.
let P = null;
const $ = (id) => document.getElementById(id);
const SIMPLE = ["name", "group_size", "extra_bedrooms_allowed", "min_bathrooms_per_person", "unknown_bathrooms_ok",
  "budget_pppw", "budget_buffer_pppw", "target_move_in", "earliest_move_in", "latest_move_in", "unknown_move_in_ok",
  "furnished", "require_student_friendly", "max_distance_km", "scrape_interval_hours", "use_routing_api"];
const LISTS = ["preferred_areas", "excluded_areas", "exclude_keywords"];
const EMAIL = ["great_score_threshold", "reminder_every_days", "max_reminders"];
const BILL_FIELDS = [
  ["elec_unit_p", "Electricity p/kWh"], ["elec_standing_p_day", "Electricity standing p/day"],
  ["gas_unit_p", "Gas p/kWh"], ["gas_standing_p_day", "Gas standing p/day"],
  ["elec_kwh_base", "Electricity kWh/yr base"], ["elec_kwh_per_person", "+ kWh/yr per person"],
  ["gas_kwh_base", "Gas kWh/yr base"], ["gas_kwh_per_person", "+ kWh/yr per person"],
  ["water_monthly", "Water £/month"], ["water_per_person_monthly", "+ water £/person"],
  ["broadband_monthly", "Broadband £/month"], ["tv_licence", "TV licence"], ["tv_licence_annual", "TV licence £/yr"],
  ["council_tax_monthly", "Council tax £/month"], ["contingency_pct", "Contingency %"],
];

function setVal(el, v) { if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? ""; }
function getVal(el) {
  if (el.type === "checkbox") return el.checked;
  if (el.type === "number") return el.value === "" ? null : Number(el.value);
  return el.value;
}

function fill() {
  SIMPLE.forEach((k) => setVal($("p-" + k), P[k]));
  LISTS.forEach((k) => ($("p-" + k).value = (P[k] || []).join(", ")));
  EMAIL.forEach((k) => setVal($("e-" + k), P.email[k]));
  $("bills-fields").innerHTML = BILL_FIELDS.map(([k, label]) => `<div class="form-row"><label>${label}</label>
    <input data-bill="${k}" type="${typeof P.bills[k] === "boolean" ? "checkbox" : "number"}" step="any"></div>`).join("");
  document.querySelectorAll("[data-bill]").forEach((el) => setVal(el, P.bills[el.dataset.bill]));
  $("weights").innerHTML = Object.keys(P.weights).map((k) => `<div class="form-row"><label>${k.replace("_", " ")} weight</label><input type="number" data-weight="${k}" value="${P.weights[k]}"></div>`).join("");
  $("sources").innerHTML = `<div class="src-row src-headings muted"><span>Site</span><span>On</span><span>Pages</span><span title="Open each listing for full details">Details</span></div>`
    + Object.entries(P.sources).map(([k, s]) => `<div class="src-row">
    <span class="src-name">${SOURCE_LABEL[k] || k}</span>
    <input type="checkbox" aria-label="${k} on" data-src="${k}" data-f="enabled" ${s.enabled ? "checked" : ""}>
    <input type="number" aria-label="${k} pages" min="1" data-src="${k}" data-f="max_pages" value="${s.max_pages}">
    <input type="checkbox" aria-label="${k} details" data-src="${k}" data-f="fetch_details" ${s.fetch_details ? "checked" : ""}></div>`).join("");
  renderPeople();
  bathHint();
  $("raw-json").value = JSON.stringify(P, null, 2);
}

function bathHint() {
  const v = Number($("p-min_bathrooms_per_person").value), n = Number($("p-group_size").value);
  $("bath-hint").textContent = v > 0 ? `≈ 1 bathroom per ${(1 / v).toFixed(1)} people → a ${n}-person house needs ${Math.ceil(n * v - 1e-9)}+ bathrooms` : "No bathroom requirement";
}

function renderPeople() {
  $("people").innerHTML = P.people.map((p, pi) => `<div class="person" data-pi="${pi}">
    <div class="person-head"><input data-pf="name" value="${esc(p.name)}" placeholder="Name">
      <input data-pf="email" value="${esc(p.email || "")}" placeholder="Email (optional)">
      <button class="small danger" data-act="del-person">Remove</button></div>
    ${(p.places || []).map((pl, li) => `<div class="place-row" data-li="${li}">
      <label class="f f-label"><span>Place</span><input data-lf="label" value="${esc(pl.label)}" placeholder="e.g. Uni, work"></label>
      <label class="f f-pc"><span>Postcode</span><input data-lf="postcode" value="${esc(pl.postcode || "")}" placeholder="NE1 7RU"></label>
      <label class="f"><span>Get there by</span><select data-lf="mode">${["walk", "cycle", "transit", "drive"].map((m) => `<option ${m === pl.mode ? "selected" : ""}>${m}</option>`).join("")}</select></label>
      <label class="f"><span>Max minutes</span><input data-lf="max_minutes" type="number" value="${pl.max_minutes}"></label>
      <label class="f"><span>Weight</span><input data-lf="weight" type="number" step="0.1" value="${pl.weight}"></label>
      <label class="f"><span>Days/week</span><input data-lf="days_per_week" type="number" value="${pl.days_per_week}"></label>
      <div class="place-foot">
        <label class="inline" title="Hide houses beyond max minutes"><input data-lf="hard_limit" type="checkbox" ${pl.hard_limit ? "checked" : ""}> Hide houses further than this</label>
        <span class="muted">${pl.lat != null ? `📍 ${pl.lat.toFixed(4)}, ${pl.lng.toFixed(4)}` : "Located when you save"}</span>
        <button class="small danger" data-act="del-place">Remove place</button>
      </div>
    </div>`).join("")}
    <button class="small" data-act="add-place">+ Add place</button>
  </div>`).join("");
}

function collect() {
  SIMPLE.forEach((k) => (P[k] = getVal($("p-" + k))));
  LISTS.forEach((k) => (P[k] = $("p-" + k).value.split(",").map((s) => s.trim()).filter(Boolean)));
  EMAIL.forEach((k) => (P.email[k] = getVal($("e-" + k))));
  document.querySelectorAll("[data-bill]").forEach((el) => (P.bills[el.dataset.bill] = getVal(el)));
  document.querySelectorAll("[data-weight]").forEach((el) => (P.weights[el.dataset.weight] = getVal(el)));
  document.querySelectorAll("[data-src]").forEach((el) => (P.sources[el.dataset.src][el.dataset.f] = getVal(el)));
  document.querySelectorAll(".person").forEach((pd) => {
    const p = P.people[pd.dataset.pi];
    pd.querySelectorAll("[data-pf]").forEach((el) => (p[el.dataset.pf] = el.value || null));
    pd.querySelectorAll(".place-row").forEach((row) => {
      const pl = p.places[row.dataset.li];
      const oldPc = pl.postcode;
      row.querySelectorAll("[data-lf]").forEach((el) => (pl[el.dataset.lf] = getVal(el) ?? null));
      if ((pl.postcode || "") !== (oldPc || "")) { pl.lat = null; pl.lng = null; }
    });
  });
}

async function geocodePlaces() {
  for (const p of P.people) for (const pl of p.places || []) {
    if (pl.postcode && pl.lat == null) {
      try { const c = await api("/api/geocode?postcode=" + encodeURIComponent(pl.postcode)); pl.lat = c.lat; pl.lng = c.lng; }
      catch (e) { toast(`Couldn't find postcode ${pl.postcode}`); }
    }
  }
}

// The save bar only shows once something in the profile sections has changed.
const setDirty = (on) => document.body.classList.toggle("dirty", on);

async function save() {
  collect();
  $("save-state").textContent = "Saving…";
  await geocodePlaces();
  try {
    const r = await api("/api/profile", { method: "PUT", body: P });
    $("save-state").textContent = "You have unsaved changes";
    toast(`Saved, re-scored ${r.rescored} listings`);
    setDirty(false);
    fill();
  } catch (e) {
    $("save-state").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

// ------------------------------------------------------------ recipients
const FREQ = ["off", "daily", "twice_daily", "weekly"];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function recipientHtml(r) {
  const cb = (k, label) => `<label class="inline"><input type="checkbox" data-rf="${k}" ${r[k] ? "checked" : ""}> ${label}</label>`;
  return `<div class="recipient" data-rid="${r.id ?? ""}">
    <div class="r-grid">
      <label class="f"><span>Name</span><input data-rf="name" value="${esc(r.name || "")}" placeholder="Name"></label>
      <label class="f"><span>Email</span><input data-rf="email" type="email" value="${esc(r.email || "")}" placeholder="email@example.com"></label>
      <label class="f"><span>Digest</span><select data-rf="digest_frequency">${FREQ.map((f) => `<option ${f === r.digest_frequency ? "selected" : ""}>${f}</option>`).join("")}</select></label>
      <label class="f"><span>At (hour, 0-23)</span><input data-rf="digest_hour" type="number" min="0" max="23" value="${r.digest_hour ?? 18}"></label>
      <label class="f"><span>Day (if weekly)</span><select data-rf="digest_weekday">${DAYS.map((d, i) => `<option value="${i}" ${i === r.digest_weekday ? "selected" : ""}>${d}</option>`).join("")}</select></label>
      <label class="f"><span>Min score</span><input data-rf="min_score" type="number" value="${r.min_score ?? 0}"></label>
      <label class="f"><span>Max places per email</span><input data-rf="max_items" type="number" value="${r.max_items ?? 20}"></label>
    </div>
    <div class="r-checks">${cb("active", "Active")}${cb("only_passing", "Only places that fit the rules")}${cb("include_new", "New places")}${cb("include_price_drops", "Price drops")}${cb("include_reminders", "Great-place reminders")}${cb("include_status_changes", "Shortlist updates")}${cb("instant_great", "Instant email for great places")}</div>
    <div class="r-actions"><button class="small primary" data-act="save-r">Save</button>
    ${r.id ? `<a class="btn small" href="/api/recipients/${r.id}/preview" target="_blank">Preview</a>
      <button class="small" data-act="send-r">Send now</button>
      <button class="small danger" data-act="del-r">Delete</button>
      <span class="muted">${r.last_digest_at ? "Last sent " + fmtDateTime(r.last_digest_at) : "Never sent"}</span>` : ""}</div>
  </div>`;
}

async function loadRecipients() {
  const list = await api("/api/recipients");
  $("recipient-list").innerHTML = list.map(recipientHtml).join("") || `<p class="muted">Nobody yet.</p>`;
}

function readRecipient(div) {
  const r = {};
  div.querySelectorAll("[data-rf]").forEach((el) => (r[el.dataset.rf] = el.type === "checkbox" ? el.checked : el.type === "number" || el.dataset.rf === "digest_weekday" ? Number(el.value) : el.value));
  return r;
}

// ------------------------------------------------------------ login allow-list
async function loadAccess() {
  const box = document.getElementById("access");
  const a = await api("/api/access");
  if (!a.configured) {
    box.innerHTML = `<p class="muted">Not connected to Cloudflare yet. Add <code>HF_CF_API_TOKEN</code> (Access: Apps and Policies: Edit) to <code>.env</code> and restart.</p>`;
    return;
  }
  if (a.error) { box.innerHTML = `<p class="err">${esc(a.error)}</p>`; return; }
  box.innerHTML = `
    <div class="access-list">${a.emails.map((e) => `<div class="access-row"><span>${esc(e)}${e === a.me ? ` <span class="muted">(you)</span>` : ""}</span>
      ${a.can_edit && e !== a.me ? `<button class="small danger" data-rm="${esc(e)}">Remove</button>` : ""}</div>`).join("") || `<p class="muted">No emails yet.</p>`}</div>
    ${a.other_rules ? `<p class="muted" style="font-size:12px">Plus ${a.other_rules} other rule(s) in the "${esc(a.policy)}" policy, managed in Cloudflare.</p>` : ""}
    ${a.can_edit ? `<form id="access-add" class="ask" style="max-width:480px"><input type="email" name="email" required placeholder="friend@example.com"><button class="primary">Add</button></form>`
      : `<p class="muted" style="font-size:12px">Only an admin can change this list.</p>`}`;
  box.querySelector("#access-add")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = e.target.email.value.trim();
    try { await api("/api/access", { method: "POST", body: { email } }); toast(`${email} can now log in`); loadAccess(); }
    catch (err) { toast(err.message, 5000); }
  });
  box.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Remove ${b.dataset.rm}? They won't be able to log in any more.`)) return;
    try { await api(`/api/access/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }); toast("Removed"); loadAccess(); }
    catch (err) { toast(err.message, 5000); }
  }));
}

async function init() {
  const data = await api("/api/profile");
  P = data.profile;
  const b = data.bills_example;
  $("bills-example").textContent = `~£${b.per_person_weekly.toFixed(0)} pppw (£${b.total_monthly.toFixed(0)}/month for the house)`;
  fill();
  loadRecipients();
  loadAccess().catch((e) => (document.getElementById("access").innerHTML = `<p class="err">${esc(e.message)}</p>`));
  const st = await api("/api/status");
  $("smtp-state").textContent = st.smtp_configured ? "SMTP configured ✓" : "SMTP not configured: set HF_SMTP_* in .env on the server";

  $("save").onclick = save;
  const profileInput = (e) => { if (!e.target.closest("#recipients-panel, #access-panel")) setDirty(true); };
  document.querySelector("main").addEventListener("input", profileInput);
  document.querySelector("main").addEventListener("change", profileInput);
  $("p-min_bathrooms_per_person").oninput = bathHint;
  $("p-group_size").oninput = bathHint;
  $("add-person").onclick = () => { collect(); P.people.push({ name: "", email: null, places: [] }); renderPeople(); setDirty(true); };
  $("people").onclick = (e) => {
    const act = e.target.dataset.act; if (!act) return;
    collect();
    const pi = Number(e.target.closest(".person").dataset.pi);
    if (act === "del-person") P.people.splice(pi, 1);
    if (act === "add-place") P.people[pi].places.push({ label: "", postcode: "", mode: "walk", max_minutes: 25, hard_limit: false, weight: 1, days_per_week: 5, lat: null, lng: null });
    if (act === "del-place") P.people[pi].places.splice(Number(e.target.closest(".place-row").dataset.li), 1);
    renderPeople();
    setDirty(true);
  };
  $("apply-json").onclick = () => {
    try { P = JSON.parse($("raw-json").value); fill(); toast("Applied, now Save"); } catch (e) { toast("Invalid JSON: " + e.message); }
  };
  $("add-recipient").onclick = () => {
    $("recipient-list").insertAdjacentHTML("beforeend", recipientHtml({ active: true, digest_frequency: "daily", digest_hour: 18, digest_weekday: 6, include_new: true, include_price_drops: true, include_reminders: true, instant_great: true, only_passing: true, min_score: 0, max_items: 20 }));
  };
  $("recipient-list").onclick = async (e) => {
    const act = e.target.dataset.act; if (!act) return;
    const div = e.target.closest(".recipient");
    const rid = div.dataset.rid;
    try {
      if (act === "save-r") {
        const body = readRecipient(div);
        await api(rid ? `/api/recipients/${rid}` : "/api/recipients", { method: rid ? "PUT" : "POST", body });
        toast("Recipient saved"); loadRecipients();
      } else if (act === "del-r") {
        if (confirm("Delete this recipient?")) { await api(`/api/recipients/${rid}`, { method: "DELETE" }); loadRecipients(); }
      } else if (act === "send-r") {
        const r = await api(`/api/recipients/${rid}/send`, { method: "POST" });
        toast(`Sent: ${r.subject}`);
      }
    } catch (err) { toast("Error: " + err.message, 5000); }
  };
}

init();
