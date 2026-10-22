// AI log (admins only): who used the AI, on which place, and roughly what it cost.
const fmtTok = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n || 0));
const fmtCost = (c) => (c ? "$" + c.toFixed(c < 1 ? 3 : 2) : "–");
const who = (u) => (u ? esc(u) : `<span class="muted">home network</span>`);

async function load() {
  const box = document.getElementById("ai-log");
  let d;
  try { d = await api("/api/ai/usage?days=" + document.getElementById("days").value); }
  catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const tot = d.people.reduce((a, p) => ({ n: a.n + p.requests, tin: a.tin + p.input_tokens, tout: a.tout + p.output_tokens, cost: a.cost + p.cost_usd }), { n: 0, tin: 0, tout: 0, cost: 0 });
  box.innerHTML = `
    <div class="stat-row">
      <div class="panel stat"><b>${tot.n}</b><span>requests</span></div>
      <div class="panel stat"><b>${d.people.length}</b><span>people</span></div>
      <div class="panel stat"><b>${fmtTok(tot.tin + tot.tout)}</b><span>tokens</span></div>
      <div class="panel stat"><b>${fmtCost(tot.cost)}</b><span>API-equivalent</span></div>
    </div>
    <p class="muted" style="font-size:12px;margin:6px 2px 14px">Runs on your Claude subscription, so the cost is what it would be on the pay-as-you-go API, not what you're charged. It's there to show who's using the most.</p>
    <div class="panel"><h3>By person</h3>
      ${d.people.length ? `<div class="table-wrap"><table class="list"><thead><tr><th>Who</th><th>Requests</th><th>Tokens in / out</th><th>Cost</th><th>Last used</th></tr></thead><tbody>
        ${d.people.map((p) => `<tr><td>${who(p.user)}${p.user && p.user === d.me ? ` <span class="muted">(you)</span>` : ""}</td>
          <td>${p.requests}${p.failed ? ` <span class="muted">(${p.failed} failed)</span>` : ""}</td>
          <td>${fmtTok(p.input_tokens)} / ${fmtTok(p.output_tokens)}</td><td>${fmtCost(p.cost_usd)}</td><td>${ago(p.last)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="muted">Nobody has used the AI in this period.</p>`}
    </div>
    <div class="panel" style="margin-top:16px"><h3>Every request</h3>
      ${d.rows.length ? `<div class="log-list">${d.rows.map((r) => `<div class="log-row${r.ok ? "" : " failed"}">
          <div class="log-top"><span>${who(r.user)}</span><span class="muted">${ago(r.at)}</span></div>
          <div>${r.kind === "overview" ? "✨ Overview" : "💬 " + esc(r.question || "Question")}
            ${r.listing_id ? ` · <a href="/listing/${r.listing_id}">${esc(r.title || "listing #" + r.listing_id)}</a>` : ""}</div>
          <div class="muted log-meta">${r.ok ? "" : `<span class="err">Failed: ${esc(r.error || "")}</span> · `}${fmtTok(r.input_tokens)} in / ${fmtTok(r.output_tokens)} out · ${fmtCost(r.cost_usd)}${r.duration_ms ? ` · ${(r.duration_ms / 1000).toFixed(1)}s` : ""}${r.model ? ` · ${esc(r.model)}` : ""}</div>
        </div>`).join("")}</div>` : `<p class="muted">No requests yet.</p>`}
    </div>`;
}

document.getElementById("days").addEventListener("change", load);
load();
