// Listing detail page.
const root = document.getElementById("detail");
const id = Number(root.dataset.id);

const LABELS = { price: "Price", commute: "Commute", bathrooms: "Bathrooms", move_in: "Move-in date", bills_included: "Bills included", photos: "Photos", preferred_area: "Preferred area" };

async function load() {
  const l = await api(`/api/listings/${id}`);
  document.title = `${l.title || l.address || "Listing"} · House finder`;
  const photos = l.photos || [];
  const bd = l.score_breakdown || {};
  const w = window.PROFILE.weights || {};
  const bills = l.bills_breakdown || {};
  root.innerHTML = `
  <div class="toolbar detail-bar">
    <a href="/" class="back" onclick="if (history.length > 1 && document.referrer.startsWith(location.origin + '/') && !document.referrer.includes('/listing/')) { history.back(); return false; }">← <span class="lbl">All listings</span></a><div class="spacer"></div>
    <button class="icon-btn ${l.favourite ? "on" : ""}" id="fav" title="Favourite">★<span class="lbl"> Favourite</span></button>
    <select id="status">${["new", "shortlisted", "contacted", "viewing", "applied", "rejected", "hidden"].map((s) => `<option ${s === l.user_status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <button id="share" title="Send to the group">Share</button>
    <a class="btn" href="${esc(l.url)}" target="_blank" rel="noopener"><span class="lbl">Open on </span>${SOURCE_LABEL[l.source] || l.source} ↗</a>
  </div>
  <div class="detail">
    <div>
      <div class="hero" id="gal">${photos.length ? `<div class="track">${photos.map((p, i) => `<div class="slide"><img data-src="${img(p)}" ${i < 2 ? `src="${img(p)}"` : ""} alt="Photo ${i + 1}" draggable="false"></div>`).join("")}</div>
          ${photos.length > 1 ? `<button class="gnav prev" data-g="-1" aria-label="Previous photo">‹</button><button class="gnav next" data-g="1" aria-label="Next photo">›</button>` : ""}
          <span class="counter" id="gcount">1 / ${photos.length}</span>
          <button class="gfull" id="gfull" aria-label="Full screen" title="Full screen (F)">⤢</button>` : `<div class="empty">No photos</div>`}
        <span class="${scoreClass(l.score)}">${Math.round(l.score || 0)}/100</span></div>
      <div class="thumbs">${photos.map((p, i) => `<img data-i="${i}" class="${i === 0 ? "on" : ""}" loading="lazy" src="${img(p)}" alt="">`).join("")}
        ${(l.floorplans || []).map((f) => `<a href="${esc(f)}" target="_blank" class="chip accent" style="align-self:center">Floorplan ↗</a>`).join("")}</div>
      <div class="panel section" style="margin-top:14px">
        <h2 style="margin:0 0 4px">${esc(l.title || l.address || "Listing")}</h2>
        <div class="muted">${esc(l.address || "")} ${l.postcode ? "· " + esc(l.postcode) : ""}</div>
        <div class="muted" style="font-size:12px;margin-top:2px">Found ${ago(l.first_seen)} · last checked on ${SOURCE_LABEL[l.source] || l.source} ${ago(l.last_seen)}</div>
        <div style="margin-top:10px">${priceBlock(l)}</div>
        <div class="facts" style="margin-top:8px">
          <span class="chip">🛏 ${l.bedrooms ?? "?"} bedrooms</span><span class="chip">🛁 ${l.bathrooms ?? "?"} bathrooms</span>
          ${availChip(l)} ${l.furnished ? `<span class="chip">${esc(l.furnished)}</span>` : ""}
          ${l.epc ? `<span class="chip">EPC ${esc(l.epc)}</span>` : ""}
          ${l.bills_included ? `<span class="chip good">Bills included</span>` : ""}
          ${l.site_status !== "active" ? `<span class="chip bad">${esc(l.site_status.replace("_", " "))}</span>` : ""}
        </div>
        ${(l.filter_reasons || []).length ? `<p class="reasons">✗ Doesn't fit: ${l.filter_reasons.map(esc).join(" · ")}</p>` : `<p class="ok">✓ Fits all your hard rules</p>`}
        ${(l.warnings || []).length ? `<p class="warnings">⚠ ${l.warnings.map(esc).join(" · ")}</p>` : ""}
      </div>
      <div class="panel section ai" id="ai"></div>
      ${(l.key_features || []).length ? `<div class="panel section"><h3>Key features</h3><ul>${l.key_features.map((k) => `<li>${esc(k)}</li>`).join("")}</ul></div>` : ""}
      <div class="panel section"><h3>Description</h3><div class="desc">${esc(l.description || l.card_text || "No description scraped yet.")}</div></div>
    </div>
    <div>
      <div class="panel section"><h3>Why this score</h3>
        ${Object.entries(bd).map(([k, v]) => `<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between"><span>${LABELS[k] || k}</span><span class="muted">${Math.round(v * 100)}% · weight ${w[k] ?? "?"}</span></div><div class="bar"><span style="width:${Math.round(v * 100)}%"></span></div></div>`).join("")}
      </div>
      <div class="panel section"><h3>Commutes</h3>
        ${(l.commutes || []).length ? `<table class="list">${l.commutes.map((c) => `<tr><td>${esc(c.person)}</td><td>${esc(c.place)}</td><td>${MODE_ICON[c.mode] || ""} ${Math.round(c.minutes)} min${c.estimated ? "*" : ""}</td><td class="muted">${c.km} km</td></tr>`).join("")}</table>
          <p class="muted" style="font-size:12px">* estimated from straight-line distance${l.geo_precision !== "exact" ? `; location is approximate (${esc(l.geo_precision || "unknown")})` : ""}</p>` : `<p class="muted">No location yet.</p>`}
        <div id="minimap" style="height:240px;border-radius:10px;margin-top:8px"></div>
      </div>
      <div class="panel section"><h3>Money</h3><div class="kv">
        <div>Rent</div><div>${money(l.rent_pppw)} pppw${l.rent_total_pcm ? ` · ${money(l.rent_total_pcm)} pcm whole house` : ""}</div>
        <div>Bills</div><div>${l.bills_included ? "Included" : l.bills_pppw_stated ? `${money(l.bills_pppw_stated)} pppw (stated by agent)` : `~${money(l.bills_pppw_estimate)} pppw (our estimate)`}</div>
        <div>All-in</div><div><b>${money(l.total_pppw)} pppw</b> · ${money((l.total_pppw || 0) * 52 / 12)} per person per month</div>
        <div>Deposit</div><div>${l.deposit ? money(l.deposit) : "?"}</div>
        <div>Min term</div><div>${l.min_term_months ? l.min_term_months + " months" : "?"}</div>
        <div>Price parsed from</div><div class="muted">${esc(l.price_source || "?")}</div>
      </div>
      ${!l.bills_included ? `<details style="margin-top:8px"><summary class="muted">Bill estimate breakdown (whole house / month)</summary><div class="kv" style="margin-top:6px">
        ${["electricity", "gas", "water", "broadband", "tv_licence", "council_tax", "contingency", "total_monthly"].map((k) => `<div>${k.replace("_", " ")}</div><div>${money(bills[k], 2)}</div>`).join("")}</div></details>` : ""}
      ${(l.price_history || []).length > 1 ? `<h4>Price history</h4>${l.price_history.map((p) => `<div class="muted">${fmtDateTime(p.at)}: ${money(p.pppw)} pppw</div>`).join("")}` : ""}
      </div>
      <div class="panel section"><h3>Agent & listing</h3><div class="kv">
        <div>Agent</div><div>${esc(l.agent_name || "?")}</div>
        <div>Phone</div><div>${l.agent_phone ? `<a href="tel:${esc(l.agent_phone)}">${esc(l.agent_phone)}</a>` : "?"}</div>
        <div>First seen</div><div>${fmtDateTime(l.first_seen)}</div>
        <div>Last seen</div><div>${fmtDateTime(l.last_seen)}</div>
        <div>Also listed on</div><div>${(l.also_on || []).map((d) => `<a href="${esc(d.url)}" target="_blank">${SOURCE_LABEL[d.source] || d.source}</a>`).join(", ") || "-"}</div>
      </div></div>
      <div class="panel section"><h3>Group votes</h3><div id="votes"></div></div>
      <div class="panel section"><h3>Notes</h3><textarea id="notes" placeholder="Viewing booked Tuesday, landlord seemed nice…">${esc(l.notes || "")}</textarea>
        <button id="save-notes" style="margin-top:6px">Save notes</button></div>
      <div class="panel section"><h3>History</h3>${(l.events || []).map((e) => `<div class="muted">${fmtDateTime(e.at)} · ${esc(e.kind.replace("_", " "))}</div>`).join("")}</div>
    </div>
  </div>`;

  if (photos.length) setupGallery(photos);
  renderAI(l);
  document.getElementById("share").onclick = async () => {
    const data = { title: l.title || l.address || "House", text: `${l.title || l.address} · ${money(l.total_pppw)} pppw all-in`, url: location.href };
    if (navigator.share) { try { await navigator.share(data); } catch (_) {} return; }
    try { await navigator.clipboard.writeText(location.href); toast("Link copied"); } catch (_) { prompt("Copy this link", location.href); }
  };
  document.getElementById("fav").onclick = async (e) => {
    l.favourite = !l.favourite;
    await patchListing(id, { favourite: l.favourite });
    e.currentTarget.classList.toggle("on", l.favourite);
  };
  document.getElementById("status").onchange = async (e) => { await patchListing(id, { user_status: e.target.value }); toast("Saved"); };
  document.getElementById("save-notes").onclick = async () => { await patchListing(id, { notes: document.getElementById("notes").value }); toast("Notes saved"); };

  const renderVotes = () => {
    const people = (window.PROFILE.people || []).map((p) => p.name).filter(Boolean);
    document.getElementById("votes").innerHTML = people.map((name) => {
      const v = (l.votes || {})[name] || 0;
      return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><span style="flex:1">${esc(name)}</span>
        <button class="icon-btn ${v === 1 ? "on" : ""}" data-person="${esc(name)}" data-v="1">👍</button>
        <button class="icon-btn ${v === -1 ? "on" : ""}" data-person="${esc(name)}" data-v="-1">👎</button></div>`;
    }).join("") || `<p class="muted">Add people in Settings to vote.</p>`;
  };
  renderVotes();
  document.getElementById("votes").onclick = async (e) => {
    const b = e.target.closest("[data-person]"); if (!b) return;
    const cur = (l.votes || {})[b.dataset.person] || 0;
    const value = cur === Number(b.dataset.v) ? 0 : Number(b.dataset.v);
    const res = await patchListing(id, { vote: { person: b.dataset.person, value } });
    l.votes = res.votes; renderVotes();
  };

  if (l.lat != null) {
    const m = L.map("minimap").setView([l.lat, l.lng], 14);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(m);
    L.circleMarker([l.lat, l.lng], { radius: 10, color: "#2563eb", fillOpacity: 0.7 }).addTo(m);
    const pts = [[l.lat, l.lng]];
    (window.PROFILE.people || []).forEach((p) => (p.places || []).forEach((pl) => {
      if (pl.lat != null) { L.marker([pl.lat, pl.lng]).addTo(m).bindPopup(`${esc(p.name)}: ${esc(pl.label)}`); pts.push([pl.lat, pl.lng]); }
    }));
    m.fitBounds(pts, { padding: [25, 25], maxZoom: 15 });
  } else {
    document.getElementById("minimap").remove();
  }
}

// Photo gallery: sliding track with arrows, keyboard, swipe/drag, thumbnails and a full-screen mode.
function setupGallery(photos) {
  const gal = document.getElementById("gal");
  const track = gal.querySelector(".track");
  const slides = [...track.querySelectorAll("img")];
  const thumbs = [...document.querySelectorAll(".thumbs img")];
  const n = photos.length;
  let i = 0;

  const load = (k) => { const im = slides[(k + n) % n]; if (im && !im.getAttribute("src")) im.src = im.dataset.src; };
  const go = (k, animate = true) => {
    i = (k + n) % n;
    track.style.transition = animate ? "" : "none";
    track.style.transform = `translateX(${-i * 100}%)`;
    [i, i + 1, i - 1].forEach(load);
    document.getElementById("gcount").textContent = `${i + 1} / ${n}`;
    thumbs.forEach((t, j) => t.classList.toggle("on", j === i));
    thumbs[i]?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: animate ? "smooth" : "auto" });
  };
  const toggleFull = (on = !gal.classList.contains("full")) => {
    gal.classList.toggle("full", on);
    document.body.classList.toggle("noscroll", on);
    go(i, false);
  };

  gal.addEventListener("click", (e) => {
    const b = e.target.closest("[data-g]");
    if (b) { e.stopPropagation(); go(i + Number(b.dataset.g)); }
    else if (e.target.closest("#gfull")) toggleFull();
  });
  thumbs.forEach((t) => t.addEventListener("click", () => go(Number(t.dataset.i))));
  document.addEventListener("keydown", (e) => {
    if (["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName)) return;
    if (e.key === "ArrowLeft") go(i - 1);
    else if (e.key === "ArrowRight") go(i + 1);
    else if (e.key === "Escape" && gal.classList.contains("full")) toggleFull(false);
    else if (e.key === "f" || e.key === "F") toggleFull();
  });

  // Swipe / drag: the track follows the pointer, then snaps to the next/previous photo.
  let startX = null, startY = 0, dx = 0, dragging = false;
  gal.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button") || (e.pointerType === "mouse" && e.button !== 0)) return;
    startX = e.clientX; startY = e.clientY; dx = 0; dragging = false;
  });
  gal.addEventListener("pointermove", (e) => {
    if (startX == null) return;
    dx = e.clientX - startX;
    if (!dragging && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(e.clientY - startY)) {
      dragging = true;
      gal.setPointerCapture(e.pointerId);
      track.style.transition = "none";
    }
    if (dragging) track.style.transform = `translateX(calc(${-i * 100}% + ${dx}px))`;
  });
  const end = () => {
    if (startX == null) return;
    const w = gal.clientWidth;
    startX = null;
    if (!dragging) return;
    dragging = false;
    go(Math.abs(dx) > Math.min(80, w * 0.15) ? i - Math.sign(dx) : i);
  };
  // Mobile browsers start scrolling/panning the page before pointer events win; claim horizontal swipes.
  let tx = null, ty = 0, horiz = null;
  gal.addEventListener("touchstart", (e) => { tx = e.touches[0].clientX; ty = e.touches[0].clientY; horiz = null; }, { passive: true });
  gal.addEventListener("touchmove", (e) => {
    if (tx == null || e.touches.length > 1) return;
    const mx = Math.abs(e.touches[0].clientX - tx), my = Math.abs(e.touches[0].clientY - ty);
    if (horiz == null && (mx > 6 || my > 6)) horiz = mx > my;
    if (horiz) e.preventDefault();
  }, { passive: false });
  gal.addEventListener("pointerup", end);
  gal.addEventListener("pointercancel", end);
  gal.addEventListener("dblclick", () => toggleFull());
  slides.forEach((im) => (im.onerror = () => im.closest(".slide").classList.add("broken")));
  go(0, false);
}

// AI overview + questions. The overview and Q&A are saved on the listing so everyone sees them.
// AI answers come short-first: the part after the marker sits behind "Show more".
// Older answers without the marker get clamped instead when they're long.
const MORE = /\n?\s*<!--\s*more\s*-->\s*\n?/i;
function mdMore(text) {
  const [short, ...rest] = (text || "").split(MORE);
  const more = rest.join("\n").trim();
  if (more) return `<div class="md">${md(short)}</div><div class="md more-body" hidden>${md(more)}</div>
    <button class="link more-btn" type="button">Show more</button>`;
  if (short.length > 450 || short.split("\n").length > 10) return `<div class="md clamp">${md(short)}</div><button class="link more-btn" type="button">Show more</button>`;
  return `<div class="md">${md(short)}</div>`;
}

function renderAI(l) {
  const box = document.getElementById("ai");
  if (!l.ai_enabled && !l.ai_overview) { box.remove(); return; }  // AI not set up: keep it out of the way
  const chat = (l.ai_chat || []).map((t) => `<div class="qa"><div class="q">${esc(t.q)}</div><div class="a">${mdMore(t.a)}</div></div>`).join("");
  const SUGGEST = ["Is it worth the price?", "How big is the living space?", "What should we check at the viewing?", "How's the kitchen?"];
  box.innerHTML = `
    <div class="ai-head"><h3>✨ AI overview</h3><div class="spacer"></div>
      ${l.ai_overview ? `<span class="muted" style="font-size:12px">${ago(l.ai_overview_at)}</span>
        ${l.ai_enabled ? `<button class="link" id="ai-regen" title="Write a fresh overview">Redo</button>` : ""}` : ""}</div>
    ${l.ai_overview ? `<div class="ai-overview">${mdMore(l.ai_overview)}</div>`
      : `<p class="muted">Get a quick rundown of this place: what's good, red flags, what the photos show, the real cost and what to ask the agent.</p>
         <button class="primary" id="ai-gen">✨ Get AI overview</button>`}
    ${l.ai_enabled ? `
    <div class="chat">${chat}</div>
    <form id="ai-ask" class="ask">
      <input name="q" autocomplete="off" placeholder="Ask anything about this place…" maxlength="2000">
      <button class="primary">Ask</button>
    </form>
    <div class="suggest">${SUGGEST.map((s) => `<button class="chip" type="button" data-q="${esc(s)}">${esc(s)}</button>`).join("")}</div>
    ${(l.ai_chat || []).length ? `<button class="link" id="ai-clear" style="font-size:12px">Clear questions</button>` : ""}` : chat}`;

  box.onclick = (e) => {
    const b = e.target.closest(".more-btn");
    if (!b) return;
    const wrap = b.parentElement, body = wrap.querySelector(".more-body"), clamp = wrap.querySelector(".clamp");
    const open = b.textContent === "Show more";
    if (body) body.hidden = !open;
    if (clamp) clamp.classList.toggle("open", open);
    b.textContent = open ? "Show less" : "Show more";
  };
  const busy = (btn, on, label) => {
    btn.disabled = on;
    if (on) { btn.dataset.label = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>${label}`; }
    else btn.innerHTML = btn.dataset.label;
  };
  const overview = async (btn) => {
    busy(btn, true, "Reading the listing… (~30s)");
    try { Object.assign(l, await api(`/api/listings/${l.id}/ai/overview`, { method: "POST" })); renderAI(l); }
    catch (e) { toast("AI overview failed: " + e.message, 5000); busy(btn, false); }
  };
  document.getElementById("ai-gen")?.addEventListener("click", (e) => overview(e.currentTarget));
  document.getElementById("ai-regen")?.addEventListener("click", (e) => overview(e.currentTarget));
  document.getElementById("ai-clear")?.addEventListener("click", async () => {
    if (!confirm("Clear all questions and answers for this place?")) return;
    await api(`/api/listings/${l.id}/ai/chat`, { method: "DELETE" });
    l.ai_chat = []; renderAI(l);
  });
  const form = document.getElementById("ai-ask");
  if (!form) return;
  const ask = async (q) => {
    q = q.trim();
    if (!q) return;
    const btn = form.querySelector("button");
    const pending = document.createElement("div");
    pending.className = "qa";
    pending.innerHTML = `<div class="q">${esc(q)}</div><div class="a muted"><span class="spin"></span>Thinking…</div>`;
    box.querySelector(".chat").appendChild(pending);
    form.q.value = "";
    busy(btn, true, "");
    try { l.ai_chat = [...(l.ai_chat || []), await api(`/api/listings/${l.id}/ai/ask`, { method: "POST", body: { question: q } })]; renderAI(l); }
    catch (e) { pending.remove(); form.q.value = q; toast("Couldn't answer: " + e.message, 5000); busy(btn, false); }
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); ask(form.q.value); });
  box.querySelector(".suggest").addEventListener("click", (e) => { if (e.target.dataset.q) ask(e.target.dataset.q); });
}

load().catch((e) => (root.innerHTML = `<div class="empty">Couldn't load listing: ${esc(e.message)}</div>`));
