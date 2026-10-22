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
