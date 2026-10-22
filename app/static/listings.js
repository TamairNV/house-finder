// Listings page: filters, sort, cards / table / map views.
const STATE_KEY = "hf-filters-v1";
let view = "grid";
let items = [];
let map = null, markers = null;

const FIELDS = ["q", "max_pppw", "min_score", "max_commute", "min_bathrooms", "available_after", "available_before", "bills_included"];
const CHECKS = ["passing", "favourite", "has_photos", "include_inactive", "include_duplicates"];

function loadState() {
  try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch (_) { return {}; }
}
function saveState(st) { try { localStorage.setItem(STATE_KEY, JSON.stringify(st)); } catch (_) {} }

function readFilters() {
  const st = { sort: document.getElementById("sort").value, view };
  FIELDS.forEach((f) => (st[f] = document.getElementById("f-" + f).value));
  CHECKS.forEach((f) => (st[f] = document.getElementById("f-" + f).checked));
  ["status", "source", "area"].forEach((g) => {
    st[g] = [...document.querySelectorAll(`#f-${g} input:checked`)].map((i) => i.value);
  });
  return st;
}

function applyState(st) {
  FIELDS.forEach((f) => { if (st[f] != null) document.getElementById("f-" + f).value = st[f]; });
  CHECKS.forEach((f) => { if (st[f] != null) document.getElementById("f-" + f).checked = st[f]; });
  if (st.sort) document.getElementById("sort").value = st.sort;
  if (st.view) setView(st.view, false);
  ["status", "source", "area"].forEach((g) => (st[g] || []).forEach((v) => {
    const el = document.querySelector(`#f-${g} input[value="${CSS.escape(v)}"]`);
    if (el) el.checked = true;
  }));
  document.getElementById("f-min_score-val").textContent = document.getElementById("f-min_score").value;
}

function queryString(st) {
  const p = new URLSearchParams();
  FIELDS.forEach((f) => { if (st[f] !== "" && st[f] != null && !(f === "min_score" && st[f] === "0")) p.set(f, st[f]); });
  p.set("passing", st.passing);
  ["favourite", "has_photos", "include_inactive", "include_duplicates"].forEach((f) => { if (st[f]) p.set(f, "true"); });
  ["status", "source", "area"].forEach((g) => st[g].forEach((v) => p.append(g, v)));
  p.set("sort", st.sort);
  return p.toString();
}

// How many filters differ from the defaults (shown as a badge on the mobile Filters button).
function activeFilterCount(st) {
  let n = FIELDS.filter((f) => st[f] !== "" && st[f] != null && !(f === "min_score" && String(st[f]) === "0")).length;
  n += ["favourite", "has_photos", "include_inactive", "include_duplicates"].filter((f) => st[f]).length;
  n += st.passing ? 0 : 1;
  n += ["status", "source", "area"].filter((g) => st[g].length).length;
  return n;
}

async function refresh() {
  const st = readFilters();
  saveState(st);
  const data = await api("/api/listings?" + queryString(st));
  items = data.items;
  document.getElementById("count").textContent = `${data.count} place${data.count === 1 ? "" : "s"}`;
  document.getElementById("filters-done").textContent = `Show ${data.count} place${data.count === 1 ? "" : "s"}`;
  const active = activeFilterCount(st);
  const badge = document.getElementById("filters-badge");
  badge.hidden = !active;
  badge.textContent = active;
  render();
  restoreScroll();
}

// Coming back from a listing: put the list where it was.
const SCROLL_KEY = "hf.listScroll";
let scrollRestored = false;
function restoreScroll() {
  if (scrollRestored) return;
  scrollRestored = true;
  try {
    const y = Number(sessionStorage.getItem(SCROLL_KEY));
    if (y && performance.getEntriesByType("navigation")[0]?.type === "back_forward") window.scrollTo(0, y);
  } catch (_) {}
}
addEventListener("pagehide", () => { try { sessionStorage.setItem(SCROLL_KEY, String(scrollY)); } catch (_) {} });

function render() {
  const el = document.getElementById("results");
  if (!items.length && view !== "votes") {
    el.innerHTML = `<div class="empty panel">Nothing matches yet. Try loosening the filters, or untick "Only places that fit my rules" to see everything scraped.<br><br><a href="/status">Check scraper status</a></div>`;
    return;
  }
  if (view === "votes") { renderVotes(el); return; }
  if (view === "grid") el.innerHTML = `<div class="grid">${items.map(cardHtml).join("")}</div>`;
  else if (view === "table") el.innerHTML = `<div class="table-wrap">${tableHtml()}</div>`;
  else renderMap(el);
}

function stepPhoto(card, dir) {
  const l = items.find((x) => x.id === Number(card.dataset.id));
  const g = card.querySelector(".gallery");
  if (!l || (l.photos || []).length < 2) return;
  const i = (Number(g.dataset.i) + dir + l.photos.length) % l.photos.length;
  g.dataset.i = i;
  const im = g.querySelector("img");
  im.classList.add("swap");
  const next = new Image();
  next.onload = next.onerror = () => { im.src = next.src; im.style.visibility = ""; im.classList.remove("swap"); };
  next.src = img(l.photos[i]);
  g.querySelector(".counter").textContent = `${i + 1}/${l.photos.length}`;
}

// Swipe left/right on a card photo to flip through its pictures.
let touch = null;
document.addEventListener("touchstart", (e) => {
  const g = e.target.closest(".card .gallery");
  touch = g ? { g, x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
}, { passive: true });
document.addEventListener("touchmove", (e) => {
  if (!touch || e.touches.length > 1) return;
  const mx = Math.abs(e.touches[0].clientX - touch.x), my = Math.abs(e.touches[0].clientY - touch.y);
  if (touch.horiz == null && (mx > 6 || my > 6)) touch.horiz = mx > my;
  if (touch.horiz) e.preventDefault();  // keep the page still while flipping photos
}, { passive: false });
document.addEventListener("touchend", (e) => {
  if (!touch) return;
  const t = e.changedTouches[0], dx = t.clientX - touch.x, dy = t.clientY - touch.y;
  if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) {
    stepPhoto(touch.g.closest(".card"), dx < 0 ? 1 : -1);
    const a = touch.g.querySelector("a");
    if (a) { const block = (ev) => { ev.preventDefault(); a.removeEventListener("click", block); }; a.addEventListener("click", block); setTimeout(() => a.removeEventListener("click", block), 400); }
  }
  touch = null;
}, { passive: true });

const isNew = (l) => l.first_seen && Date.now() - new Date(l.first_seen + "Z") < 48 * 3600 * 1000;

const STATUSES = ["new", "shortlisted", "contacted", "viewing", "applied", "rejected", "hidden"];
const STATUS_LABEL = { new: "New", shortlisted: "Shortlisted", contacted: "Contacted", viewing: "Viewing", applied: "Applied", rejected: "Rejected", hidden: "Hidden" };

function cardHtml(l) {
  const photos = l.photos || [];
  const also = (l.also_on || []).map((d) => `<a href="${esc(d.url)}" target="_blank" rel="noopener">${SOURCE_LABEL[d.source] || d.source}</a>`).join(", ");
  const issues = [...(l.filter_reasons || []).map((r) => `<span class="bad">✗ ${esc(r)}</span>`), ...(l.warnings || []).map((w) => `<span class="warn">⚠ ${esc(w)}</span>`)];
  const billsNote = l.bills_included ? "· bills included" : l.bills_pppw_stated ? `+ ${money(l.bills_pppw_stated)} bills` : `+ ~${money(l.bills_pppw_estimate)} bills (est.)`;
  const src = SOURCE_LABEL[l.source] || l.source;
  return `<article class="card ${l.favourite ? "fav" : ""} ${l.passes_filters ? "" : "failing"}" data-id="${l.id}">
    <div class="gallery" data-i="0">
      ${photos.length ? `<a href="/listing/${l.id}"><img loading="lazy" src="${img(photos[0])}" alt="" onerror="this.style.visibility='hidden'"></a>
        ${photos.length > 1 ? `<button class="nav prev" data-act="prev" aria-label="Previous photo">‹</button><button class="nav next" data-act="next" aria-label="Next photo">›</button>` : ""}
        <span class="counter">1/${photos.length}</span>` : `<a href="/listing/${l.id}" class="noimg">No photos</a>`}
      <span class="${scoreClass(l.score)}" title="Match score">${Math.round(l.score || 0)}</span>
      <button class="fav-btn ${l.favourite ? "on" : ""}" data-act="fav" aria-label="Favourite" title="Favourite">★</button>
      ${isNew(l) ? `<span class="new-badge">NEW</span>` : ""}
    </div>
    <div class="body">
      <div class="price-row">
        <div class="price">${money(l.total_pppw)}<small> pppw all-in</small></div>
        <a class="src-link" href="${esc(l.url)}" target="_blank" rel="noopener" title="Open on ${esc(src)}">${esc(src)} ↗</a>
      </div>
      <div class="price-sub">${money(l.rent_pppw)} rent ${esc(billsNote)}</div>
      <a class="title" href="/listing/${l.id}">${esc(l.title || l.address || "Listing")}</a>
      <div class="addr">${esc((l.address || "").split(",")[0])}${l.area ? ` · ${esc(l.area)}` : ""}</div>
      <div class="facts">
        <span class="chip">🛏 ${l.bedrooms ?? "?"}</span>
        <span class="chip">🛁 ${l.bathrooms ?? "?"}</span>
        ${availChip(l)}
        ${l.bills_included ? `<span class="chip good">Bills inc</span>` : ""}
        ${l.furnished ? `<span class="chip">${esc(l.furnished[0].toUpperCase() + l.furnished.slice(1))}</span>` : ""}
        ${l.site_status !== "active" ? `<span class="chip bad">${esc(l.site_status.replace("_", " "))}</span>` : ""}
      </div>
      ${(l.commutes || []).length ? `<div class="commutes">${commuteChips(l)}</div>` : ""}
      ${issues.length ? `<div class="issues">${issues.join("")}</div>` : ""}
      ${also ? `<div class="also">Also on ${also}</div>` : ""}
    </div>
    ${cardFoot(l)}
  </article>`;
}

function cardFoot(l) {
  const vals = Object.values(l.votes || {});
  const up = vals.filter((v) => v > 0).length, down = vals.filter((v) => v < 0).length;
  const mine = (l.votes || {})[myName()] || 0;
  const voting = groupNames().length ? `<div class="vote-pill" role="group" aria-label="Vote">
      <button class="${mine > 0 ? "on" : ""}" data-act="up" title="I like it">👍<b>${up || ""}</b></button>
      <button class="${mine < 0 ? "on" : ""}" data-act="down" title="Not for me">👎<b>${down || ""}</b></button>
    </div>` : "";
  return `<div class="card-foot">${voting}
    <label class="status-pill st-${esc(l.user_status)}"><span class="sr">Status</span><select data-act="status">${STATUSES.map((s) =>
      `<option value="${s}" ${s === l.user_status ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}</select></label>
  </div>`;
}

function tableHtml() {
  const rows = items.map((l) => `<tr data-id="${l.id}">
    <td>${l.photos?.length ? `<img loading="lazy" src="${img(l.photos[0])}">` : ""}</td>
    <td><span class="${scoreClass(l.score)}" style="position:static">${Math.round(l.score || 0)}</span></td>
    <td><a href="/listing/${l.id}">${esc(l.title || l.address || "Listing")}</a><div class="muted">${esc(l.area || "")}</div></td>
    <td><b>${money(l.total_pppw)}</b><div class="muted">${l.bills_included ? "bills inc" : "est. bills"}</div></td>
    <td>${l.bedrooms ?? "?"} / ${l.bathrooms ?? "?"}</td>
    <td>${l.available_from ? fmtDate(l.available_from) : "?"}</td>
    <td>${l.worst_commute_min != null ? Math.round(l.worst_commute_min) + " min" : "?"}</td>
    <td>${SOURCE_LABEL[l.source] || l.source}${(l.also_on || []).length ? ` +${l.also_on.length}` : ""}</td>
    <td>${esc(l.user_status)}${l.favourite ? " ★" : ""}</td>
  </tr>`).join("");
  return `<table class="list"><thead><tr><th></th><th>Score</th><th>Place</th><th>£ pppw</th><th>Bed/Bath</th><th>Available</th><th>Worst commute</th><th>Site</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
}

const WALK_KMH = 4.8;
function renderMap(el) {
  el.innerHTML = `<div class="map-wrap"><div id="map"></div>
    <div class="map-legend panel"><span><i class="dot great"></i>great</span><span><i class="dot ok"></i>fits rules</span><span><i class="dot meh"></i>doesn't fit</span>
      <label class="inline"><input type="checkbox" id="rings" ${loadRings() ? "checked" : ""}> walk rings</label></div></div>`;
  map = L.map("map", { zoomControl: !matchMedia("(max-width: 900px)").matches });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
  const great = window.PROFILE.email.great_score_threshold || 80;
  const pts = [];
  // Houses: price pills coloured by score, best ones drawn on top.
  [...items].filter((l) => l.lat != null).sort((a, b) => (a.score || 0) - (b.score || 0)).forEach((l) => {
    const cls = l.score >= great ? "great" : l.passes_filters ? "ok" : "meh";
    const ups = Object.values(l.votes || {}).filter((v) => v > 0).length;
    const icon = L.divIcon({ className: "", iconSize: null, iconAnchor: [0, 0],
      html: `<div class="pin ${cls}${l.favourite ? " fav" : ""}">${l.favourite ? "★ " : ""}£${Math.round(l.total_pppw || 0)}${ups ? ` <small>👍${ups}</small>` : ""}</div>` });
    L.marker([l.lat, l.lng], { icon, riseOnHover: true }).addTo(map).bindPopup(() => `<div class="map-pop">
      ${l.photos?.length ? `<img src="${img(l.photos[0])}">` : ""}
      <a class="title" href="/listing/${l.id}">${esc(l.title || l.address)}</a>
      <div><b>${money(l.total_pppw)}</b> pppw all-in · ${l.bedrooms} bed · ${l.bathrooms ?? "?"} bath · score ${Math.round(l.score || 0)}</div>
      <div class="commutes">${commuteChips(l)}</div>
      ${groupNames().length ? `<div class="faces">${voteFaces(l)}</div>` : ""}
      ${l.geo_precision !== "exact" ? `<div class="muted">Approximate location</div>` : ""}</div>`, { maxWidth: 260 });
    pts.push([l.lat, l.lng]);
  });
  // People's places, with rough walking-time rings (straight line at walking pace).
  const rings = L.layerGroup();
  (window.PROFILE.people || []).forEach((p) => (p.places || []).forEach((pl) => {
    if (pl.lat == null) return;
    const icon = L.divIcon({ className: "", iconSize: null, html: `<div class="place-pin">📍 ${esc(pl.label || p.name)}</div>` });
    L.marker([pl.lat, pl.lng], { icon, zIndexOffset: 1000 }).addTo(map).bindPopup(`${esc(p.name)}: ${esc(pl.label)}`);
    [10, 20, 30].forEach((min) => L.circle([pl.lat, pl.lng], { radius: (WALK_KMH * 1000 * min) / 60 / 1.25, color: "#2563eb", weight: 1,
      opacity: 0.5, fill: min === 10, fillOpacity: 0.05, dashArray: "4 4", interactive: false }).addTo(rings));
    pts.push([pl.lat, pl.lng]);
  }));
  if (loadRings()) rings.addTo(map);
  document.getElementById("rings").onchange = (e) => { saveRings(e.target.checked); e.target.checked ? rings.addTo(map) : rings.remove(); };
  if (pts.length) map.fitBounds(pts, { padding: [30, 30] });
  else map.setView([54.978, -1.6178], 13);
}
function loadRings() { try { return localStorage.getItem("hf.rings") !== "0"; } catch (_) { return true; } }
function saveRings(on) { try { localStorage.setItem("hf.rings", on ? "1" : "0"); } catch (_) {} }

// Votes view: everything anyone has voted on (or starred/shortlisted), ranked by the group's verdict.
const ACTIVE_STATUSES = ["shortlisted", "contacted", "viewing", "applied"];
async function renderVotes(el) {
  el.innerHTML = `<p class="muted">Loading votes…</p>`;
  const all = (await api("/api/listings?passing=false&include_inactive=true&limit=2000&sort=score")).items;
  const names = groupNames(), me = myName();
  const voted = all.filter((l) => Object.keys(l.votes || {}).length || l.favourite || ACTIVE_STATUSES.includes(l.user_status));
  const net = (l) => Object.values(l.votes || {}).reduce((a, v) => a + v, 0);
  voted.sort((a, b) => net(b) - net(a) || (b.score || 0) - (a.score || 0));
  const todo = me ? voted.filter((l) => !(l.votes || {})[me]) : [];
  const row = (l) => {
    const waiting = names.filter((n) => !(l.votes || {})[n]);
    const mine = (l.votes || {})[me] || 0;
    return `<div class="vote-row" data-id="${l.id}">
      ${l.photos?.length ? `<img loading="lazy" src="${img(l.photos[0])}">` : `<div class="noimg"></div>`}
      <div class="vote-main">
        <a class="title" href="/listing/${l.id}">${l.favourite ? "★ " : ""}${esc(l.title || l.address || "Listing")}</a>
        <div class="muted">${money(l.total_pppw)} pppw · ${esc(l.area || "")}${l.site_status !== "active" ? ` · <span class="err">${esc(l.site_status.replace("_", " "))}</span>` : ""}</div>
        <div class="faces">${voteFaces(l)}${waiting.length && waiting.length < names.length ? `<span class="muted waiting">waiting on ${esc(waiting.join(", "))}</span>` : ""}</div>
      </div>
      <div class="vote-side"><b class="net ${net(l) > 0 ? "up" : net(l) < 0 ? "down" : ""}">${net(l) > 0 ? "+" : ""}${net(l)}</b>
        <div><button class="icon-btn vote ${mine > 0 ? "on" : ""}" data-act="up">👍</button><button class="icon-btn vote ${mine < 0 ? "on" : ""}" data-act="down">👎</button></div></div>
    </div>`;
  };
  el.innerHTML = `<div class="votes-view">
    <div class="panel votes-head">${whoPicker()}<span class="muted">${names.length} people · ${voted.length} places on the board</span></div>
    ${!names.length ? `<div class="empty panel">Add the group in Settings → People to start voting.</div>` : ""}
    ${todo.length ? `<div class="panel"><h3>Your turn (${todo.length})</h3><p class="muted" style="margin-top:-4px">Places the group is looking at that you haven't voted on.</p>${todo.map(row).join("")}</div>` : ""}
    <div class="panel"><h3>Group ranking</h3>${voted.length ? voted.map(row).join("")
      : `<p class="muted">Nothing yet. Tap 👍 / 👎 on a card, star a place or mark it shortlisted and it shows up here.</p>`}</div>
  </div>`;
  el.querySelector("[data-act=who]")?.addEventListener("change", (e) => { setMyName(e.target.value); renderVotes(el); });
  el.onclick = async (e) => {
    const act = e.target.dataset.act;
    if (act !== "up" && act !== "down") return;
    const l = all.find((x) => x.id === Number(e.target.closest("[data-id]").dataset.id));
    if (await vote(l, act === "up" ? 1 : -1)) renderVotes(el);
  };
}

function setView(v, rerender = true) {
  view = v;
  document.querySelectorAll("#view button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  if (rerender) { saveState(readFilters()); render(); }
}

async function init() {
  const meta = await api("/api/meta");
  const mk = (id, opts) => (document.getElementById(id).innerHTML = opts.map((o) =>
    `<label><input type="checkbox" value="${esc(o.value)}"> ${esc(o.label)}</label>`).join(""));
  mk("f-status", meta.statuses.map((s) => ({ value: s, label: s })));
  mk("f-source", meta.sources.map((s) => ({ value: s.name, label: `${s.label} (${s.count})` })));
  mk("f-area", meta.areas.map((a) => ({ value: a.name, label: `${a.name} (${a.count})` })));
  applyState(loadState());
  const urlSort = new URLSearchParams(location.search).get("sort");
  if (urlSort && document.querySelector(`#sort option[value="${CSS.escape(urlSort)}"]`)) document.getElementById("sort").value = urlSort;

  let timer;
  const debounced = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };
  document.getElementById("filters").addEventListener("input", (e) => {
    if (e.target.id === "f-min_score") document.getElementById("f-min_score-val").textContent = e.target.value;
    debounced();
  });
  document.getElementById("sort").addEventListener("change", refresh);
  const sheet = (open) => {
    document.getElementById("filters").classList.toggle("open", open);
    document.body.classList.toggle("noscroll", open && matchMedia("(max-width: 900px)").matches);
  };
  document.getElementById("filters-toggle").addEventListener("click", () => sheet(!document.getElementById("filters").classList.contains("open")));
  document.getElementById("filters-close").addEventListener("click", () => sheet(false));
  document.getElementById("filters-done").addEventListener("click", () => { sheet(false); window.scrollTo({ top: 0 }); });
  document.getElementById("view").addEventListener("click", (e) => { if (e.target.dataset.v) setView(e.target.dataset.v); });
  document.getElementById("reset").addEventListener("click", () => { saveState({ view }); location.reload(); });

  document.getElementById("results").addEventListener("click", async (e) => {
    const actEl = e.target.closest("[data-act]");
    const act = actEl?.dataset.act;
    const card = e.target.closest("[data-id]");
    if (!act || !card) {
      if (view === "table" && card && !e.target.closest("a")) location.href = `/listing/${card.dataset.id}`;
      return;
    }
    const l = items.find((x) => x.id === Number(card.dataset.id));
    if (act === "prev" || act === "next") {
      e.preventDefault();
      stepPhoto(card, act === "next" ? 1 : -1);
    } else if (act === "fav") {
      l.favourite = !l.favourite;
      await patchListing(l.id, { favourite: l.favourite });
      actEl.classList.toggle("on", l.favourite);
      card.classList.toggle("fav", l.favourite);
    } else if (act === "up" || act === "down") {
      if (!myName()) { setView("votes"); toast("Pick your name to vote"); return; }
      if (!(await vote(l, act === "up" ? 1 : -1))) return;
      card.querySelector(".card-foot").outerHTML = cardFoot(l);
    }
  });
  document.getElementById("results").addEventListener("change", async (e) => {
    if (e.target.dataset.act !== "status") return;
    const id = Number(e.target.closest("[data-id]").dataset.id);
    e.target.parentElement.className = `status-pill st-${e.target.value}`;
    const l = items.find((x) => x.id === id);
    if (l) l.user_status = e.target.value;
    await patchListing(id, { user_status: e.target.value });
    toast(`Marked as ${e.target.value}`);
    if (["hidden", "rejected"].includes(e.target.value)) refresh();
  });
  refresh();
}

init();
