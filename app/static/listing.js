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
  <div class="toolbar">
    <a href="/">← All listings</a><div class="spacer"></div>
    <button class="icon-btn ${l.favourite ? "on" : ""}" id="fav">★ Favourite</button>
    <select id="status">${["new", "shortlisted", "contacted", "viewing", "applied", "rejected", "hidden"].map((s) => `<option ${s === l.user_status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <a class="btn" href="${esc(l.url)}" target="_blank" rel="noopener">Open on ${SOURCE_LABEL[l.source] || l.source} ↗</a>
  </div>
  <div class="detail">
    <div>
      <div class="hero">${photos.length ? `<img id="hero" src="${img(photos[0])}">` : `<div class="empty">No photos</div>`}
        <span class="${scoreClass(l.score)}">${Math.round(l.score || 0)}/100</span></div>
      <div class="thumbs">${photos.map((p, i) => `<img data-i="${i}" class="${i === 0 ? "on" : ""}" loading="lazy" src="${img(p)}">`).join("")}
        ${(l.floorplans || []).map((f) => `<a href="${esc(f)}" target="_blank" class="chip accent" style="align-self:center">Floorplan ↗</a>`).join("")}</div>
      <div class="panel section" style="margin-top:14px">
        <h2 style="margin:0 0 4px">${esc(l.title || l.address || "Listing")}</h2>
        <div class="muted">${esc(l.address || "")} ${l.postcode ? "· " + esc(l.postcode) : ""}</div>
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

  document.querySelectorAll(".thumbs img").forEach((t) => t.addEventListener("click", () => {
    document.getElementById("hero").src = img(photos[t.dataset.i]);
    document.querySelectorAll(".thumbs img").forEach((x) => x.classList.toggle("on", x === t));
  }));
  document.addEventListener("keydown", (e) => {
    if (!photos.length || !["ArrowLeft", "ArrowRight"].includes(e.key) || e.target.tagName === "TEXTAREA") return;
    const cur = document.querySelector(".thumbs img.on");
    let i = Number(cur?.dataset.i || 0) + (e.key === "ArrowRight" ? 1 : -1);
    i = (i + photos.length) % photos.length;
    document.querySelector(`.thumbs img[data-i="${i}"]`).click();
  });
  document.getElementById("fav").onclick = async (e) => {
    l.favourite = !l.favourite;
    await patchListing(id, { favourite: l.favourite });
    e.target.classList.toggle("on", l.favourite);
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

load().catch((e) => (root.innerHTML = `<div class="empty">Couldn't load listing: ${esc(e.message)}</div>`));
