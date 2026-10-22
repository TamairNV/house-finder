// Shared helpers for all pages (no build step, plain ES2020).
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const img = (u) => (u ? "/img?u=" + encodeURIComponent(u) : "");
const money = (n, dp = 0) => (n == null ? "?" : "£" + Number(n).toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp }));
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "?");
const fmtDateTime = (iso) => (iso ? new Date(iso + (iso.endsWith("Z") || iso.includes("+") ? "" : "Z")).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
const MODE_ICON = { walk: "🚶", cycle: "🚲", transit: "🚌", drive: "🚗" };
const SOURCE_LABEL = { rightmove: "Rightmove", zoopla: "Zoopla", openrent: "OpenRent", spareroom: "SpareRoom", sturents: "StuRents", afs: "AFS", unihomes: "UniHomes" };

function toast(msg, ms = 2500) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function scoreClass(score) {
  const great = (window.PROFILE?.email?.great_score_threshold) ?? 80;
  return score >= great ? "score great" : "score";
}

function priceBlock(l) {
  const billsNote = l.bills_included ? "bills included"
    : l.bills_pppw_stated ? `+${money(l.bills_pppw_stated)} bills (stated)`
    : `+~${money(l.bills_pppw_estimate)} bills (est.)`;
  return `<div class="price">${money(l.total_pppw)} <small>pppw all-in</small></div>
          <div class="muted" style="font-size:12px">${money(l.rent_pppw)} rent ${esc(billsNote)}${l.rent_total_pcm ? ` · ${money(l.rent_total_pcm)} pcm total` : ""}</div>`;
}

function commuteChips(l) {
  return (l.commutes || []).map((c) => {
    const over = c.minutes > c.max_minutes;
    return `<span class="chip ${over ? "warn" : ""}" title="${esc(c.person)} → ${esc(c.place)}, ${c.km} km${c.estimated ? " (estimate)" : ""}">${MODE_ICON[c.mode] || ""} ${esc(c.person)}: ${Math.round(c.minutes)}m</span>`;
  }).join("");
}

function availChip(l) {
  if (!l.available_from) return `<span class="chip warn">Date unknown</span>`;
  if (l.available_now) return `<span class="chip bad">Available now</span>`;
  return `<span class="chip">From ${fmtDate(l.available_from)}</span>`;
}

async function patchListing(id, body) {
  return api(`/api/listings/${id}`, { method: "PATCH", body });
}

// "5m ago" style relative time for ISO timestamps (naive ones are UTC from the API).
function ago(iso) {
  if (!iso) return "never";
  const t = new Date(iso + (iso.endsWith("Z") || iso.includes("+") ? "" : "Z"));
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

// Tiny Markdown renderer for AI answers: headings, bullet/numbered lists, bold, inline code, paragraphs.
// Input is escaped first, so model output can't inject HTML.
function md(src) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|\W)\*([^*\s][^*]*?)\*(?=\W|$)/g, "$1<i>$2</i>");
  const out = [];
  let list = null, para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
  for (const raw of String(src || "").split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { flushPara(); flushList(); out.push(`<h4>${inline(m[1])}</h4>`); continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      const tag = /^\s*\d/.test(line) ? "ol" : "ul";
      flushPara();
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[1]);
      continue;
    }
    flushList();
    para.push(line);
  }
  flushPara(); flushList();
  return out.join("");
}

// Header "last updated" indicator, refreshed every minute.
async function refreshUpdated() {
  const el = document.getElementById("updated");
  if (!el) return;
  try {
    const u = await api("/api/updated");
    const next = u.next_scrape ? new Date(u.next_scrape).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : null;
    el.innerHTML = (u.scraping ? `<span class="pulse"></span>Checking sites…` : `<span class="upd-dot"></span>Updated ${ago(u.last_scrape)}`)
      + (u.new_24h ? `<a class="new-pill" href="/?sort=newest">${u.new_24h} new</a>` : "");
    el.title = (u.last_scrape ? `Last scrape finished ${fmtDateTime(u.last_scrape)}` : "No scrape yet") + (next ? ` · next check ${next}` : "");
  } catch (_) { /* header indicator is best-effort */ }
}
document.addEventListener("DOMContentLoaded", () => { refreshUpdated(); setInterval(refreshUpdated, 60000); });

// ------------------------------------------------------------ group voting
// Who's voting on this device: matched from the login email (person's email in Settings), else picked once.
const groupNames = () => (window.PROFILE?.people || []).map((p) => p.name).filter(Boolean);
function myName() {
  if (window.ME) return window.ME;
  try { const n = localStorage.getItem("hf.me"); return groupNames().includes(n) ? n : null; } catch (_) { return null; }
}
function setMyName(n) { try { localStorage.setItem("hf.me", n); } catch (_) {} }
function whoPicker() {
  if (window.ME) return `<span class="muted">Voting as <b>${esc(window.ME)}</b></span>`;
  const me = myName();
  return `<label class="who">Voting as <select data-act="who"><option value="">pick your name…</option>${groupNames().map((n) =>
    `<option ${n === me ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>`;
}
// Thumbs for one person: click again to clear. Resolves to the listing's new votes.
async function vote(l, value, person = myName()) {
  if (!person) { toast("Pick your name first (Votes tab or the listing page)"); return null; }
  const cur = (l.votes || {})[person] || 0;
  const res = await patchListing(l.id, { vote: { person, value: cur === value ? 0 : value } });
  l.votes = res.votes;
  return l.votes;
}
// Little row of initials: green = 👍, red = 👎, grey = not voted yet.
function voteFaces(l) {
  return groupNames().map((n) => {
    const v = (l.votes || {})[n] || 0;
    return `<span class="face ${v > 0 ? "up" : v < 0 ? "down" : ""}" title="${esc(n)}: ${v > 0 ? "👍" : v < 0 ? "👎" : "not voted"}">${esc(n.slice(0, 2))}</span>`;
  }).join("");
}
