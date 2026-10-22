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

async function refresh() {
  const st = readFilters();
  saveState(st);
  const data = await api("/api/listings?" + queryString(st));
  items = data.items;
  document.getElementById("count").textContent = `${data.count} place${data.count === 1 ? "" : "s"}`;
  render();
}

function render() {
  const el = document.getElementById("results");
  if (!items.length) {
    el.innerHTML = `<div class="empty panel">Nothing matches yet. Try loosening the filters, or untick "Only places that fit my rules" to see everything scraped.<br><br><a href="/status">Check scraper status</a></div>`;
    return;
  }
  if (view === "grid") el.innerHTML = `<div class="grid">${items.map(cardHtml).join("")}</div>`;
  else if (view === "table") el.innerHTML = tableHtml();
  else renderMap(el);
}

function cardHtml(l) {
  const photos = l.photos || [];
  const also = (l.also_on || []).map((d) => `<a class="chip accent" href="${esc(d.url)}" target="_blank" rel="noopener">${SOURCE_LABEL[d.source] || d.source}</a>`).join("");
  const reasons = (l.filter_reasons || []).length ? `<div class="reasons">✗ ${l.filter_reasons.map(esc).join(" · ")}</div>` : "";
  const warns = (l.warnings || []).length ? `<div class="warnings">⚠ ${l.warnings.map(esc).join(" · ")}</div>` : "";
  const bathRatio = l.bathrooms && l.bedrooms ? ` (1:${(window.PROFILE.group_size / l.bathrooms).toFixed(1)})` : "";
  return `<article class="card ${l.favourite ? "fav" : ""} ${l.passes_filters ? "" : "failing"}" data-id="${l.id}">
    <div class="gallery" data-i="0">
      ${photos.length ? `<a href="/listing/${l.id}"><img loading="lazy" src="${img(photos[0])}" alt=""></a>
        ${photos.length > 1 ? `<button class="nav prev" data-act="prev">‹</button><button class="nav next" data-act="next">›</button>` : ""}
        <span class="counter">1/${photos.length}</span>` : `<a href="/listing/${l.id}" class="noimg">No photos</a>`}
      <span class="${scoreClass(l.score)}">${Math.round(l.score || 0)}</span>
      <span class="src-badge">${SOURCE_LABEL[l.source] || l.source}</span>
    </div>
    <div class="body">
      <a class="title" href="/listing/${l.id}">${esc(l.title || l.address || "Listing")}</a>
      <div class="addr">${esc(l.address || "")}${l.area ? ` · ${esc(l.area)}` : ""}</div>
      ${priceBlock(l)}
      <div class="facts">
        <span class="chip">🛏 ${l.bedrooms ?? "?"}</span>
        <span class="chip">🛁 ${l.bathrooms ?? "?"}${bathRatio}</span>
        ${availChip(l)}
        ${l.bills_included ? `<span class="chip good">Bills inc</span>` : ""}
        ${l.furnished ? `<span class="chip">${esc(l.furnished)}</span>` : ""}
        ${l.site_status !== "active" ? `<span class="chip bad">${esc(l.site_status.replace("_", " "))}</span>` : ""}
      </div>
      <div class="commutes">${commuteChips(l)}</div>
      ${reasons}${warns}
      ${also ? `<div class="facts"><span class="muted" style="font-size:12px">Also on:</span>${also}</div>` : ""}
    </div>
    <div class="actions">
      <button class="icon-btn ${l.favourite ? "on" : ""}" data-act="fav" title="Favourite">★</button>
      <select data-act="status">${["new", "shortlisted", "contacted", "viewing", "applied", "rejected", "hidden"].map((s) => `<option ${s === l.user_status ? "selected" : ""}>${s}</option>`).join("")}</select>
      <div class="spacer"></div>
      <a href="${esc(l.url)}" target="_blank" rel="noopener" class="btn">Open on site ↗</a>
    </div>
  </article>`;
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

function renderMap(el) {
  el.innerHTML = `<div id="map"></div>`;
  map = L.map("map");
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
  const pts = [];
  items.filter((l) => l.lat != null).forEach((l) => {
    const color = l.score >= (window.PROFILE.email.great_score_threshold || 80) ? "#059669" : l.passes_filters ? "#2563eb" : "#9ca3af";
    const m = L.circleMarker([l.lat, l.lng], { radius: 9, color, fillColor: color, fillOpacity: 0.8, weight: 2 }).addTo(map);
    m.bindPopup(`${l.photos?.length ? `<img src="${img(l.photos[0])}" style="width:220px;height:140px;object-fit:cover;border-radius:6px">` : ""}
      <div><b><a href="/listing/${l.id}">${esc(l.title || l.address)}</a></b></div>
      <div>${money(l.total_pppw)} pppw all-in · ${l.bedrooms} bed · ${l.bathrooms ?? "?"} bath · score ${Math.round(l.score || 0)}</div>
      ${l.geo_precision !== "exact" ? `<div class="muted">approx. location</div>` : ""}`);
    pts.push([l.lat, l.lng]);
  });
  (window.PROFILE.people || []).forEach((p) => (p.places || []).forEach((pl) => {
    if (pl.lat != null) {
      L.marker([pl.lat, pl.lng]).addTo(map).bindPopup(`${esc(p.name)}: ${esc(pl.label)}`);
      pts.push([pl.lat, pl.lng]);
    }
  }));
  if (pts.length) map.fitBounds(pts, { padding: [30, 30] });
  else map.setView([54.978, -1.6178], 13);
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

  let timer;
  const debounced = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };
  document.getElementById("filters").addEventListener("input", (e) => {
    if (e.target.id === "f-min_score") document.getElementById("f-min_score-val").textContent = e.target.value;
    debounced();
  });
  document.getElementById("sort").addEventListener("change", refresh);
  document.getElementById("view").addEventListener("click", (e) => { if (e.target.dataset.v) setView(e.target.dataset.v); });
  document.getElementById("reset").addEventListener("click", () => { saveState({ view }); location.reload(); });

  document.getElementById("results").addEventListener("click", async (e) => {
    const act = e.target.dataset.act;
    const card = e.target.closest("[data-id]");
    if (!act || !card) {
      if (view === "table" && card && !e.target.closest("a")) location.href = `/listing/${card.dataset.id}`;
      return;
    }
    const l = items.find((x) => x.id === Number(card.dataset.id));
    if (act === "prev" || act === "next") {
      e.preventDefault();
      const g = card.querySelector(".gallery");
      let i = Number(g.dataset.i) + (act === "next" ? 1 : -1);
      i = (i + l.photos.length) % l.photos.length;
      g.dataset.i = i;
      g.querySelector("img").src = img(l.photos[i]);
      g.querySelector(".counter").textContent = `${i + 1}/${l.photos.length}`;
    } else if (act === "fav") {
      l.favourite = !l.favourite;
      await patchListing(l.id, { favourite: l.favourite });
      e.target.classList.toggle("on", l.favourite);
      card.classList.toggle("fav", l.favourite);
    }
  });
  document.getElementById("results").addEventListener("change", async (e) => {
    if (e.target.dataset.act !== "status") return;
    const id = Number(e.target.closest("[data-id]").dataset.id);
    await patchListing(id, { user_status: e.target.value });
    toast(`Marked as ${e.target.value}`);
    if (["hidden", "rejected"].includes(e.target.value)) refresh();
  });
  refresh();
}

init();
