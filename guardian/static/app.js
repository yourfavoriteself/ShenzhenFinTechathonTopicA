const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (c) => (c < 0 ? "-" : "") + "$" + (Math.abs(c) / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (p) => (p * 100).toFixed(1) + "%";
let STATE = null;
let SCEN = [];
let currentScenario = null;

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || r.statusText);
  if (data && data.state) render(data.state);
  return data;
}

function toast(msg) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3500);
}

const STATUS = {
  executed: ["Executed", "b-ok", "ok"], pending_confirmation: ["Needs your approval", "b-warn", "warn"],
  blocked: ["Blocked", "b-bad", "bad"], rejected: ["Rejected", "b-bad", "bad"], declined: ["Declined", "b-info", ""],
  quarantined: ["Quarantined", "b-violet", ""], reversed: ["Reversed", "b-info", ""], released: ["Released", "b-info", ""],
};
const badge = (s) => { const [l, c] = STATUS[s] || [s, "b-info"]; return `<span class="badge ${c}">${esc(l)}</span>`; };
const when = (iso) => { const d = new Date(iso); return d.toLocaleString("en-AU", { timeZone: "UTC", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); };

// ------------------------------------------------------------------ overview
function render(s) {
  STATE = s;
  $("#clock").textContent = when(s.clock);
  $("#llm-badge").textContent = s.llm ? "LLM assistant on" : "offline deterministic mode";
  $("#decoy-banner").classList.toggle("hidden", !s.decoy);
  const exp = s.exposure;
  $("#kpis").innerHTML = `
    <div class="card"><div class="k">Balance${s.decoy ? "" : " (available)"}</div><div class="v">${usd(s.decoy ? s.total : s.available)}</div>
      <div class="s">${s.decoy ? "" : (s.quarantine ? `${usd(s.quarantine)} frozen in quarantine · ` : "") + `${usd(s.unallocated)} unallocated`}</div></div>
    <div class="card"><div class="k">Worst-case loss to an unknown payee</div><div class="v">${usd(exp.with_guardian)}</div>
      <div class="s">without envelopes: ${usd(exp.without_guardian)}</div>
      <div class="meter"><div style="width:${exp.without_guardian ? Math.max(2, 100 * exp.with_guardian / exp.without_guardian) : 0}%;background:var(--brand2)"></div></div></div>
    <div class="card"><div class="k">Autonomy guarantee</div><div class="v">≤ ${pct(s.risk_model.alpha)}</div>
      <div class="s">P(auto-executed and bad), conformal risk control · test: ${pct(s.risk_model.test_auto_and_bad_rate)}</div></div>
    <div class="card"><div class="k">Signed audit entries</div><div class="v">${s.audit_entries}</div>
      <div class="s">hash chain + Ed25519</div></div>`;
  $("#envelopes").innerHTML = s.envelopes.map((e) => `
    <div class="env"><div class="name">${esc(e.name)} ${e.protected ? '<span class="lock">🔒 protected</span>' : ""}</div>
      <div class="bal">${usd(e.balance)}</div><div class="purpose">${esc(e.purpose)}</div>
      <div>${e.rules.map((r) => `<span class="chip">${esc(r)}</span>`).join("")}</div></div>`).join("");
  $("#transactions").innerHTML = s.transactions.length ? `<table><tr><th>Time</th><th>What</th><th>Envelope</th><th>By</th><th class="num">Amount</th><th>Status</th><th></th></tr>
    ${s.transactions.map((t) => {
      const env = t.envelope_id || t.to_envelope_id || "";
      const what = t.kind === "payment" ? `${esc(t.payee_name)} <span class="chip">${esc(t.rail.replace(/_/g, " "))}</span>` :
        t.kind === "deposit" ? `Income: ${esc(t.payee_name)}` : t.kind === "allocation" ? "Budget allocation" :
        t.kind === "quarantine" ? `Frozen (duress): ${esc(t.payee_name)}` : t.kind === "release" ? "Released from quarantine" :
        t.kind === "reversal" ? "Reversal" : esc(t.kind + (t.memo ? ": " + t.memo : ""));
      const sign = ["payment", "quarantine"].includes(t.kind) ? -1 : 1;
      const rev = t.kind === "payment" && t.status === "executed" && !s.decoy && !["crypto", "gift_card"].includes(t.rail)
        ? `<button class="small" onclick="revertTx('${t.id}')">Revert</button>` : "";
      return `<tr><td class="mono">${when(t.ts)}</td><td>${what}</td><td>${esc(env)}</td><td>${esc(t.initiator.replace("_", " "))}</td>
        <td class="num">${t.kind === "allocation" ? `<span class="hint">→ ${usd(t.amount)}</span>` : usd(sign * t.amount)}</td><td>${badge(t.status)}</td><td>${rev}</td></tr>`;
    }).join("")}</table>` : `<div class="empty">No transactions yet. Press “Pay day”.</div>`;
  $("#pending").innerHTML = s.pending.length ? s.pending.map((p) => `
    <div class="notif"><b>${usd(p.amount)}</b> to ${esc(p.payee)} <span class="meta">requested by ${esc(p.initiator.replace("_", " "))}</span>
      <div style="margin-top:6px"><button class="small primary" onclick="confirmPending('${p.id}', true)">Approve</button>
      <button class="small" onclick="confirmPending('${p.id}', false)">Decline</button></div></div>`).join("") : `<div class="empty">Nothing to approve.</div>`;
  $("#outbox").innerHTML = s.outbox.length ? s.outbox.map((m) => `
    <div class="notif">${m.kind === "duress" ? '<span class="badge b-violet">silent alert</span> ' : ""}${esc(m.message)}
      <div class="meta">${when(m.ts)} · to ${esc(m.to)} via ${esc(m.channel)}</div></div>`).join("") : `<div class="empty">No notifications.</div>`;
}

async function revertTx(id) {
  const r = await api("/api/revert", { tx_id: id });
  toast(r.result.new_trust !== null ? `Reverted. Guardian's autonomy for '${r.result.category}' is now ×${r.result.new_trust}.` : "Reverted.");
}
async function confirmPending(id, approve) {
  const r = await api("/api/confirm", { pending_id: id, approve });
  toast(`${r.outcome.status}: ${r.outcome.explanation}`);
}

// ------------------------------------------------------------------ outcome rendering
function outcomeHTML(o) {
  if (!o) return "";
  const [label, cls, box] = STATUS[o.status] || [o.status, "b-info", ""];
  let html = `<div class="result ${box}"><div class="big-status">${esc(label)}</div><div>${esc(o.explanation)}</div>`;
  if (o.signals && o.signals.length) html += `<p><span class="badge b-violet">possible coercion</span> ${esc(o.signals.join("; "))}</p>`;
  if (o.receipt) html += receiptHTML(o.receipt);
  html += `</div>`;
  if (o.risk) {
    const r = o.risk;
    const pW = Math.min(100, r.p_error * 100), elW = Math.min(100, (100 * r.expected_loss) / Math.max(1, r.el_limit * 2));
    html += `<div class="panel"><h3>Risk-priced autonomy</h3><div class="kv">
      <div class="k">Decision</div><div><b>${esc(r.decision)}</b></div>
      <div class="k">P(error)</div><div>${pct(r.p_error)} (autonomy threshold ${pct(r.conformal_threshold)})
        <div class="meter"><div style="width:${pW}%;background:${r.p_error > r.conformal_threshold ? "var(--bad)" : "var(--ok)"}"></div></div></div>
      <div class="k">Reversibility of rail</div><div>${pct(r.reversibility)}</div>
      <div class="k">Expected loss</div><div>${usd(r.expected_loss)} = P(error) × ${usd(r.amount)} × (1 − ${r.reversibility})
        <div class="meter"><div style="width:${elW}%;background:${r.expected_loss > r.el_limit ? "var(--bad)" : "var(--ok)"}"></div></div></div>
      <div class="k">Autonomy limit</div><div>${usd(r.el_limit)} (trust ×${r.trust})</div>
      <div class="k">Reasons</div><div>${r.reasons.map(esc).join("<br>")}</div></div></div>`;
  }
  if (o.policy && o.policy.checks && o.policy.checks.length) {
    html += `<div class="panel" style="margin-top:12px"><h3>Purpose-bound money: envelope checks</h3>
      ${o.policy.checks.map((c) => `<div class="check ${c.ok ? "ok" : "bad"}"><span class="i">${c.ok ? "✓" : "✗"}</span>
      <span><b>${esc(c.envelope_name)}</b> <span class="d">${c.ok ? "allows this payment" : esc(c.reasons.join("; "))}</span></span></div>`).join("")}</div>`;
  } else if (o.policy && o.policy.blocked_by === "blocklist") {
    html += `<div class="panel" style="margin-top:12px"><h3>Blocklist</h3>${esc(o.policy.summary)}</div>`;
  }
  return html;
}

function receiptHTML(r) {
  return `<div class="panel" style="margin-top:10px"><div class="kv">
    <div class="k">Reference</div><div class="mono">${esc(r.reference)}</div>
    <div class="k">To</div><div>${esc(r.to)} <span class="mono">${esc(r.account)}</span></div>
    <div class="k">Amount</div><div>${usd(r.amount)}</div><div class="k">Status</div><div>${esc(r.status)}</div>
    <div class="k">Available balance</div><div>${usd(r.available_balance)}</div></div></div>`;
}

// ------------------------------------------------------------------ pay
const PRESETS = {
  grocery: { payee_name: "FreshMart Supermarket", account: "card-merchant:freshmart", amount: 45.2, rail: "card", category: "groceries", country: "AU" },
  friend: { payee_name: "Tom Lee", account: "payid:tom.lee@uni-mail.example", amount: 120, rail: "domestic_transfer", category: "personal", country: "AU" },
  tuition: { payee_name: "Southgate University", account: "GB33SGUN20000012345678", amount: 300, rail: "international_transfer", category: "education", country: "GB" },
  big: { payee_name: "Fast Cash", account: "payid:fastcash88@mail.example", amount: 1200, rail: "domestic_transfer", category: "unknown", country: "AU" },
  crypto: { payee_name: "CoinVault", account: "bc1qgpma2x56kf7hl794ju05xjtx49sv2plyxpgc4q", amount: 400, rail: "crypto", category: "investment", country: "XX" },
};
$$("[data-preset]").forEach((b) => b.addEventListener("click", () => {
  const p = PRESETS[b.dataset.preset], f = $("#pay-form");
  Object.entries(p).forEach(([k, v]) => { f.elements[k].value = v; });
}));
$("#pay-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target), body = Object.fromEntries(f.entries());
  body.amount = parseFloat(body.amount);
  try { const r = await api("/api/pay", body); $("#pay-result").innerHTML = outcomeHTML(r.outcome); }
  catch (err) { toast(err.message); }
});

// ------------------------------------------------------------------ scam shield
async function loadScenarios() {
  SCEN = await api("/api/scenarios");
  $("#msg-presets").innerHTML = SCEN.map((s, i) => `<button data-i="${i}" class="${s.kind === "scam" ? "" : "ghost"}">${esc(s.title)}</button>`).join("");
  $$("#msg-presets button").forEach((b) => b.addEventListener("click", () => {
    const s = SCEN[b.dataset.i]; currentScenario = s;
    $("#msg-sender").value = s.sender; $("#msg-text").value = s.text;
    $("#msg-bait").disabled = s.kind !== "scam"; $("#msg-result").innerHTML = ""; $("#bait-result").innerHTML = "";
  }));
}
$("#msg-analyze").addEventListener("click", async () => {
  const r = await api("/api/message", { text: $("#msg-text").value, sender: $("#msg-sender").value });
  const a = r.analysis, cls = { scam: "bad", suspicious: "warn", low_risk: "ok" }[a.verdict];
  let html = `<div class="result ${cls}"><div class="big-status">${esc(a.verdict.replace("_", " ").toUpperCase())} · score ${a.score}</div>
    <div class="meter"><div style="width:${a.score * 100}%;background:var(--${cls === "ok" ? "ok" : cls === "warn" ? "warn" : "bad"})"></div></div>
    <div>${esc(a.explanation)}</div>${a.llm_note ? `<p class="hint">LLM note: ${esc(a.llm_note)}</p>` : ""}</div>`;
  if (a.signals.length) html += `<div class="panel"><h3>Signals</h3>${a.signals.map((s) => `<div class="check bad"><span class="i">!</span><span>${esc(s)}</span></div>`).join("")}</div>`;
  if (a.indicators.length) html += `<div class="panel" style="margin-top:12px"><h3>Extracted indicators</h3><table><tr><th>Type</th><th>Value</th><th>Checksum</th></tr>
    ${a.indicators.map((i) => `<tr><td>${esc(i.kind)}</td><td class="ioc">${esc(i.value)}</td><td>${i.valid ? "valid" : "invalid"}</td></tr>`).join("")}</table></div>`;
  if (r.outcome) html += `<h3>What Guardian did with the payment request</h3>` + outcomeHTML(r.outcome);
  $("#msg-result").innerHTML = html;
});
$("#msg-bait").addEventListener("click", async () => {
  if (!currentScenario) return;
  $("#bait-result").innerHTML = `<p class="hint">Baiter persona is chatting with the scammer…</p>`;
  const r = await api("/api/bait", { scenario_id: currentScenario.id });
  const rep = r.report;
  window._policeReport = r.police_report;
  $("#bait-result").innerHTML = `<h2>Scam Baiter session</h2>
    <div class="cards"><div class="card"><div class="k">Indicators in first message</div><div class="v">${rep.indicators_in_opening}</div></div>
      <div class="card"><div class="k">Indicators after baiting</div><div class="v">${rep.indicators.length}</div></div>
      <div class="card"><div class="k">Scammer time wasted</div><div class="v">~${rep.scammer_minutes_wasted} min</div></div>
      <div class="card"><div class="k">Money sent / PII leaks</div><div class="v">${usd(rep.money_sent)} / 0</div><div class="s">${rep.pii_leaks_blocked} unsafe replies filtered</div></div></div>
    <div class="two-col" style="margin-top:14px"><div class="chat">${rep.turns.map((t) => `<div class="bubble ${t.speaker}"><div class="who">${t.speaker === "scammer" ? "Scammer" : "Guardian decoy persona" + (t.tactic ? " · " + esc(t.tactic) : "")}</div>${esc(t.text)}</div>`).join("")}</div>
    <div><div class="panel"><h3>Added to Mia's blocklist (closed loop)</h3>${r.added.map((v) => `<div class="ioc">${esc(v)}</div>`).join("") || '<div class="empty">already known</div>'}
      <p class="hint">Any future payment to these accounts is blocked before the risk model even runs.</p>
      <button onclick="downloadReport()">Download police / bank report (JSON)</button></div></div></div>`;
});
function downloadReport() {
  const blob = new Blob([JSON.stringify(window._policeReport, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "scam_intelligence_report.json"; a.click();
}

// ------------------------------------------------------------------ agent shopping
$$("[data-agent]").forEach((b) => b.addEventListener("click", async () => {
  const r = await api("/api/shop", { agent: b.dataset.agent, description: $("#shop-desc").value,
    keywords: $("#shop-kw").value.split(",").map((s) => s.trim()).filter(Boolean), categories: ["electronics"], max_total: parseFloat($("#shop-max").value) });
  const x = r.result, v = x.verdict;
  let html = `<div class="result ${v.approved ? "ok" : "bad"}"><div class="big-status">${v.approved ? "Cart approved" : "Cart rejected"}</div>
    <div>${esc(v.summary)}</div><p class="hint">Agent: ${esc(x.agent_id)} · trust score now ${x.agent_trust} · rationale: “${esc(x.cart.rationale)}”</p></div>
    <div class="panel"><h3>Proposed cart (${usd(x.cart.items.reduce((a, i) => a + i.price, 0))})</h3>
    ${x.cart.items.map((i) => `<div class="check"><span class="i">•</span><span>${esc(i.name)} — ${usd(i.price)} <span class="chip">${esc(i.category)}</span>${i.recurring ? '<span class="chip">recurring</span>' : ""} <span class="d">${esc(i.seller)}</span></span></div>`).join("")}</div>
    <div class="panel" style="margin-top:12px"><h3>Deterministic checks against the signed mandate</h3>
    ${v.checks.map((c) => `<div class="check ${c.ok ? "ok" : c.hard ? "bad" : "soft"}"><span class="i">${c.ok ? "✓" : c.hard ? "✗" : "!"}</span><span><b>${esc(c.name)}</b> <span class="d">${esc(c.detail)}</span></span></div>`).join("")}</div>`;
  const hidden = v.findings.filter((f) => f.severity === "high");
  if (hidden.length) html += `<div class="panel" style="margin-top:12px"><h3>Hidden instructions found on the pages behind this choice</h3>
    ${hidden.map((f) => `<div class="finding"><b>${esc(f.page)}</b> · ${esc(f.technique)}<code>${esc(f.text)}</code></div>`).join("")}</div>`;
  if (x.payment) html += `<h3>Payment with a single-use token</h3>` + outcomeHTML(x.payment);
  if (x.alternative) html += `<div class="panel" style="margin-top:12px"><h3>Clean alternative from unmanipulated pages</h3>
    ${x.alternative.items.map((i) => `${esc(i.name)} — ${usd(i.price)} (${esc(i.seller)})`).join("<br>")}</div>`;
  $("#shop-result").innerHTML = html;
}));

// ------------------------------------------------------------------ duress
$("#duress-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  const body = { payee_name: f.payee_name, account: f.account, amount: parseFloat(f.amount), rail: "domestic_transfer", category: "personal", pin: f.pin };
  try {
    const r = await api("/api/pay", body);
    const bo = await api("/api/backoffice");
    const o = r.outcome;
    $("#duress-result").innerHTML = `<div class="split">
      <div><h3>What the attacker sees on the phone</h3><div class="phone">
        ${o.status === "executed" ? `<div class="tick">✓</div><div style="text-align:center;font-weight:800;font-size:17px">${esc(o.receipt.title)}</div>
        <div style="text-align:center;font-size:24px;font-weight:800;margin:6px 0">${usd(o.receipt.amount)}</div>
        <div class="kv" style="grid-template-columns:90px 1fr"><div class="k">To</div><div>${esc(o.receipt.to)}</div>
          <div class="k">Ref</div><div class="mono">${esc(o.receipt.reference)}</div><div class="k">Status</div><div>${esc(o.receipt.status)}</div>
          <div class="k">Balance</div><div>${usd(o.receipt.available_balance)}</div></div>` : `<div class="big-status">${esc(o.status)}</div>${esc(o.explanation)}`}
      </div></div>
      <div class="truth"><h3>What really happened (bank fraud console)</h3><div class="panel kv">
        <div class="k">Money that left the bank</div><div><b>$0.00</b></div>
        <div class="k">Frozen in quarantine</div><div><b>${usd(bo.quarantine)}</b></div>
        <div class="k">Silent alerts sent</div><div>${bo.alerts.map((a) => `${esc(a.to)} (${esc(a.channel)})`).join("<br>") || "none"}</div>
        <div class="k">Payee account</div><div>blocklisted</div>
        <div class="k">Session</div><div>${bo.decoy_session ? "decoy mode until the owner logs in with the normal PIN" : "normal"}</div></div>
        ${bo.alerts[0] ? `<div class="notif" style="margin-top:10px"><span class="badge b-violet">SMS to ${esc(bo.alerts[0].to)}</span><br>${esc(bo.alerts[0].message)}</div>` : ""}
        <p class="hint">Later, safe again, Mia logs in with her normal PIN (banner at the top) and releases the money.</p>
        <button id="release-btn">Release frozen funds</button></div></div>`;
    $("#release-btn").addEventListener("click", async () => {
      try { const x = await api("/api/duress/resolve", {}); toast(`${usd(x.result.released)} returned to Mia's envelopes.`); }
      catch (err) { toast(err.message); }
    });
  } catch (err) { toast(err.message); }
});
$("#login-btn").addEventListener("click", async () => {
  try { const s = await api("/api/session/login", { pin: $("#login-pin").value }); render(s); toast(s.decoy ? "Still in decoy mode." : "Welcome back, Mia. Review the incident below."); }
  catch (err) { toast(err.message); }
});

// ------------------------------------------------------------------ audit
async function loadAudit() {
  const a = await api("/api/audit?n=300");
  const v = a.verification;
  $("#audit-status").innerHTML = `<div class="result ${v.ok ? "ok" : "bad"}"><div class="big-status">${v.ok ? "Chain intact" : "Chain BROKEN"}</div>
    ${v.checked} entries verified · head hash <span class="mono">${esc((v.head_hash || "").slice(0, 32))}…</span><br>
    <span class="hint">Guardian public key (Ed25519): <span class="mono">${esc(a.public_key)}</span></span></div>`;
  $("#audit-table").innerHTML = `<table><tr><th>#</th><th>Time</th><th>Actor</th><th>Event</th><th>Data</th><th>Hash</th></tr>
    ${a.entries.slice().reverse().map((e) => `<tr><td>${e.seq}</td><td class="mono">${when(e.ts)}</td><td>${esc(e.actor)}</td><td><b>${esc(e.event)}</b></td>
    <td class="mono" style="max-width:520px;word-break:break-word">${esc(JSON.stringify(e.data)).slice(0, 260)}</td><td class="mono">${e.hash.slice(0, 12)}…</td></tr>`).join("")}</table>`;
}
$$("[data-tamper]").forEach((b) => b.addEventListener("click", async () => {
  try {
    const r = await api("/api/audit/tamper", { mode: b.dataset.tamper });
    const v = r.verification;
    $("#tamper-result").innerHTML = `<div class="result bad"><div class="big-status">Tampering detected</div>
      A copy of the log with entry #${r.seq} ${b.dataset.tamper === "edit" ? "edited" : b.dataset.tamper === "delete" ? "deleted" : "swapped with the next one"}
      fails verification at entry #${v.first_bad_seq}: <b>${esc(v.reason)}</b>. The original log still verifies: ${r.original.ok}.</div>`;
  } catch (err) { toast(err.message); }
}));

// ------------------------------------------------------------------ red team
$("#rt-run").addEventListener("click", async () => {
  $("#rt-result").innerHTML = `<p class="hint">Running ${"21"} attacks and 8 controls…</p>`;
  const r = await api("/api/redteam", {});
  const s = r.summary, max = Math.max(s.lost_unguarded_agent, 1);
  const bar = (label, v, color) => `<div class="bar"><div>${label}</div><div class="meter" style="height:16px"><div class="fill" style="width:${Math.max(0.5, (100 * v) / max)}%;background:${color}"></div></div><div class="num"><b>${usd(v)}</b></div></div>`;
  $("#rt-result").innerHTML = `<div class="cards" style="margin-top:14px">
    <div class="card"><div class="k">Attacks prevented</div><div class="v">${s.prevented} / ${s.attacks}</div><div class="s">${s.bounded} loss-bounded · ${s.succeeded} succeeded</div></div>
    <div class="card"><div class="k">Money at risk</div><div class="v">${usd(s.money_at_risk)}</div></div>
    <div class="card"><div class="k">Lost with Guardian</div><div class="v">${usd(s.lost_guardian)}</div></div>
    <div class="card"><div class="k">Legitimate actions</div><div class="v">${s.controls_autonomous + s.controls_one_tap} / ${s.controls}</div><div class="s">${s.controls_autonomous} autonomous · ${s.controls_one_tap} one tap · ${s.controls_blocked} wrongly blocked</div></div></div>
    <h2>Money lost across all attacks</h2><div class="panel bars">${bar("Unguarded AI agent", s.lost_unguarded_agent, "var(--bad)")}${bar("Static bank rules", s.lost_static_rules, "var(--warn)")}${bar("Guardian", s.lost_guardian, "var(--ok)")}</div>
    <h2>Attacks</h2><table><tr><th>ID</th><th>Attack</th><th>Class</th><th>Guardian</th><th class="num">Lost</th><th class="num">Static rules</th><th class="num">Unguarded</th></tr>
    ${r.attacks.map((a) => `<tr title="${esc(a.detail)}"><td>${a.id}</td><td>${esc(a.name)}</td><td>${esc(a.category)}</td><td><span class="badge ${a.outcome === "prevented" ? "b-ok" : a.outcome === "bounded" ? "b-warn" : "b-bad"}">${esc(a.outcome)}</span></td>
      <td class="num">${usd(a.lost)}</td><td class="num">${usd(a.static)}</td><td class="num">${usd(a.unguarded)}</td></tr>`).join("")}</table>
    <h2>Legitimate controls</h2><table><tr><th>ID</th><th>Action</th><th>Guardian</th></tr>
    ${r.controls.map((c) => `<tr title="${esc(c.detail)}"><td>${c.id}</td><td>${esc(c.name)}</td><td>${badge(c.guardian)}</td></tr>`).join("")}</table>`;
});

// ------------------------------------------------------------------ story
$("#story-run").addEventListener("click", async () => {
  const r = await api("/api/story", {});
  const s = r.summary;
  $("#story-result").innerHTML = `<div class="cards" style="margin-top:14px">
    <div class="card"><div class="k">Steps</div><div class="v">${s.steps}</div></div>
    <div class="card"><div class="k">Money protected</div><div class="v">${usd(s.scam_and_attack_money_protected)}</div></div>
    <div class="card"><div class="k">Audit entries</div><div class="v">${s.audit_entries}</div><div class="s">verified: ${s.audit_ok}</div></div>
    <div class="card"><div class="k">Blocklist</div><div class="v">${s.blocklist_size}</div></div></div>
    <div class="panel timeline" style="margin-top:14px">${r.steps.map((x) => `<div class="item"><div class="when">${esc(x.when)}<br><span class="chip">${esc(x.feature)}</span></div>
      <div><div class="t">${x.n}. ${esc(x.title)}</div><div>${esc(x.result)}</div></div></div>`).join("")}</div>`;
});

// ------------------------------------------------------------------ header and tabs
$$("[data-act]").forEach((b) => b.addEventListener("click", async () => {
  const act = b.dataset.act;
  try {
    if (act === "reset") { await api("/api/reset", {}); $$("[id$='-result']").forEach((el) => (el.innerHTML = "")); toast("Sandbox reset."); }
    else if (act === "bills") { const r = await api("/api/bills/run", {}); toast(r.outcomes.length ? r.outcomes.map((o) => `${o.status}: ${o.explanation.split(".")[0]}`).join(" | ") : "No bills due today."); }
    else { const r = await api("/api/clock", { action: act }); if (act === "payday") toast("Income received and split into envelopes."); }
  } catch (err) { toast(err.message); }
}));
$$("#tabs button").forEach((b) => b.addEventListener("click", () => {
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "audit") loadAudit();
  if (b.dataset.tab === "overview") api("/api/state").then(render);
}));

api("/api/state").then(render);
loadScenarios();
