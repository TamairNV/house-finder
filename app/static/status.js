// Status page: scrape runs, emails, manual triggers.
const $ = (id) => document.getElementById(id);

async function load() {
  const s = await api("/api/status");
  const meta = await api("/api/meta");
  $("overview").innerHTML = `
    <div>Scraper</div><div>${s.scraping ? "<b>running now…</b>" : "idle"}</div>
    <div>Next scheduled run</div><div>${s.next_scrape ? fmtDateTime(s.next_scrape) : "scheduler off"}</div>
    <div>Listings stored</div><div>${meta.totals.all} (${meta.totals.passing} fit your rules)</div>
    <div>Email (SMTP)</div><div>${s.smtp_configured ? '<span class="ok">configured</span>' : '<span class="err">not configured</span>'}</div>
    <div>Routing API</div><div>${s.routing_api ? "openrouteservice" : "distance estimates only"}</div>
    <div>Headless browser</div><div>${s.playwright ? "enabled" : "off"}</div>
    ${meta.sources.map((x) => `<div>${esc(x.label)}</div><div>${x.count} listings</div>`).join("")}`;
  $("events").innerHTML = Object.entries(s.events_24h).map(([k, v]) => `<div>${esc(k.replace("_", " "))}</div><div>${v}</div>`).join("") || "<div class='muted'>No activity</div><div></div>";
  $("runs").innerHTML = s.runs.map((r) => `<tr><td>${fmtDateTime(r.started_at)}</td><td>${SOURCE_LABEL[r.source] || r.source}</td>
    <td>${r.finished_at ? (r.ok ? '<span class="ok">ok</span>' : '<span class="err">failed</span>') : "running"}</td>
    <td>${r.requests}</td><td>${r.found}</td><td>${r.new}</td><td>${r.updated}</td>
    <td>${r.error ? `<details><summary class="err">error</summary><pre class="err">${esc(r.error)}</pre></details>` : ""}</td></tr>`).join("");
  $("emails").innerHTML = s.emails.map((e) => `<tr><td>${fmtDateTime(e.sent_at)}</td><td>${esc(e.kind)}</td><td>${esc(e.subject)}</td><td>${e.count}</td>
    <td>${e.ok ? '<span class="ok">sent</span>' : `<span class="err" title="${esc(e.error)}">failed</span>`}</td></tr>`).join("");
}

$("scrape").onclick = async () => { const r = await api("/api/scrape", { method: "POST" }); toast(r.message); setTimeout(load, 1500); };
$("rescore").onclick = async () => { const r = await api("/api/rescore", { method: "POST" }); toast(`Re-scored ${r.rescored}`); load(); };
load();
setInterval(load, 15000);
