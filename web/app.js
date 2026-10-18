"use strict";
// Guardian client. The device key lives in WebCrypto (non-extractable) and signs the hash of the
// mandate shown on each card: what you see is what you sign. All text is inserted as text, never HTML.

const $ = (s) => document.querySelector(s);
const feed = $("#feed");
let lastSeq = 0;
let device = null;

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) el.append(k instanceof Node ? k : String(k));
  return el;
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ------------------------------------------------------------------ device key

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));

function idb() {
  return new Promise((ok, fail) => {
    const r = indexedDB.open("guardian", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("keys");
    r.onsuccess = () => ok(r.result);
    r.onerror = () => fail(r.error);
  });
}
async function idbGet(k) { const db = await idb(); return new Promise((ok) => { const q = db.transaction("keys").objectStore("keys").get(k); q.onsuccess = () => ok(q.result); q.onerror = () => ok(null); }); }
async function idbPut(k, v) { const db = await idb(); return new Promise((ok) => { const t = db.transaction("keys", "readwrite"); t.objectStore("keys").put(v, k); t.oncomplete = ok; t.onerror = ok; }); }

async function makeKey() {
  try {
    const kp = await crypto.subtle.generateKey({name: "Ed25519"}, false, ["sign", "verify"]);
    return {alg: "Ed25519", kp};
  } catch (e) {
    const kp = await crypto.subtle.generateKey({name: "ECDSA", namedCurve: "P-256"}, false, ["sign", "verify"]);
    return {alg: "ES256", kp};
  }
}

async function loadDevice() {
  let d = await idbGet("device").catch(() => null);
  if (!d) { d = await makeKey(); await idbPut("device", d).catch(() => {}); }
  const pub = b64(await crypto.subtle.exportKey("raw", d.kp.publicKey));
  device = {alg: d.alg, kp: d.kp, pub};
  await api("/api/device", {alg: d.alg, public_key: pub});
  $("#device").textContent = `device key: ${d.alg} …${pub.slice(-8)}`;
}

async function signMandate(hash) {
  const data = new TextEncoder().encode(`guardian-mandate:${hash}`);
  const algo = device.alg === "Ed25519" ? {name: "Ed25519"} : {name: "ECDSA", hash: "SHA-256"};
  return b64(await crypto.subtle.sign(algo, device.kp.privateKey, data));
}

// ------------------------------------------------------------------ feed

function costLine(d) {
  if (d.template) return h("div", {class: "meta"}, "Repeated from your template: 0 tokens, no model called");
  const c = d.cost;
  if (!c || !c.calls) return null;
  return h("div", {class: "meta"},
    `Models: ${c.calls} call${c.calls > 1 ? "s" : ""}, level ${c.levels.join("–")}, ${c.input_tokens + c.output_tokens} tokens, `
    + `${c.cost_rub.toFixed(2)} ₽${c.estimated ? " (estimate, stub)" : ""}`);
}

function steps(d) {
  if (!d.steps || !d.steps.length) return null;
  return h("details", {}, h("summary", {}, "kernel pipeline"),
    h("ul", {}, d.steps.map((s) => h("li", {}, `${s.step}: ${["allow", "ask", "deny"][s.verdict]}`
      + (s.reasons.length ? ` — ${s.reasons.join("; ")}` : "")))));
}

function optionButtons(opts) {
  return h("div", {class: "opts"}, (opts || []).map((o) => h("button", {
    class: o.covers ? "covers" : "",
    onclick: async (e) => { e.target.disabled = true; await run(api(`/api/options/${o.id}`, {})); },
  }, o.title)));
}

function renderCard(it) {
  const d = it.data, c = d.card;
  const risk = ["low", "medium", "high"][c.risk];
  const el = h("div", {class: `card risk-${c.risk}`, "data-pending": d.pending_id});
  el.append(h("h3", {}, c.title, h("span", {class: `badge r${c.risk}`}, `${risk} risk`)));
  if (d.summary) el.append(h("div", {class: "summary"}, d.summary));
  const iv = c.intervention;
  let answers = null, pin = null;
  if (iv) {
    const box = h("div", {class: "intervention"},
      h("h4", {}, `⚠ Possible scam call · level ${iv.level}: ${iv.name}`),
      h("p", {class: "explain"}, iv.explain),
      h("ul", {}, iv.signals.map((x) => h("li", {}, x))),
      iv.wait_until ? h("div", {class: "meta"}, `Pause: can be signed after ${iv.wait_until.slice(11, 16)}`) : null,
      iv.after ? h("div", {class: "meta"}, iv.after) : null);
    if (iv.questions) {
      answers = {};
      for (const [k, q] of Object.entries(iv.questions)) {
        const name = `${d.pending_id}-${k}`;
        const yesR = h("input", {type: "radio", name, value: "yes"}), noR = h("input", {type: "radio", name, value: "no"});
        answers[k] = [yesR, noR];
        box.append(h("div", {class: "q"}, h("span", {}, q), h("label", {}, yesR, "yes"), h("label", {}, noR, "no")));
      }
      pin = h("input", {type: "password", name: "pin", placeholder: "PIN", inputmode: "numeric", maxlength: "6"});
      box.append(h("div", {class: "q"}, h("span", {}, "Your PIN"), pin));
    }
    el.append(box);
  }
  const tbl = h("table", {class: "fields"});
  for (const f of c.fields) {
    tbl.append(h("tr", {class: f.changed ? "changed" : ""},
      h("td", {}, f.name.replace("_", " ")),
      h("td", {}, f.previous ? h("span", {class: "prev"}, f.previous) : null, f.value,
        f.badge ? h("div", {class: "src" + (f.warning ? " warn" : "")}, (f.warning ? "⚠ " : "✔ ") + f.badge) : null)));
  }
  el.append(tbl);
  if (c.reasons.length) el.append(h("ul", {class: "reasons"}, c.reasons.map((r) => h("li", {}, r))));
  el.append(h("div", {class: "cols"},
    h("div", {}, h("h4", {}, "Guardian will be able to"), h("ul", {}, c.can.map((x) => h("li", {}, x)))),
    h("div", {}, h("h4", {}, "and will not be able to"), h("ul", {}, c.cannot.map((x) => h("li", {}, x))))));
  let standing = null;
  if (c.standing_offer) {
    standing = h("input", {type: "checkbox"});
    el.append(h("label", {class: "standing"}, standing, c.standing_offer));
  }
  let typed = null;
  if (c.type_amount) {
    typed = h("input", {placeholder: "type the amount to confirm", inputmode: "decimal"});
    el.append(h("div", {class: "actions"}, typed));
  }
  const status = h("span", {class: "meta"});
  const yes = h("button", {class: c.hold_to_confirm ? "hold" : ""}, c.hold_to_confirm ? "Hold to confirm" : "Confirm",
    c.hold_to_confirm ? h("span", {class: "bar"}) : null);
  const no = h("button", {class: "secondary", onclick: async () => { lock(); await run(api(`/api/cards/${d.pending_id}/decline`, {})); }}, "Decline");
  const lock = () => { yes.disabled = true; no.disabled = true; if (standing) standing.disabled = true; };
  const unlock = () => { yes.disabled = false; no.disabled = false; if (standing) standing.disabled = false; };
  const go = async () => {
    lock();
    try {
      const useStanding = !!(standing && standing.checked);
      const hash = useStanding ? c.standing_mandate_hash : c.mandate_hash;
      const sig = await signMandate(hash);
      const body = {alg: device.alg, public_key: device.pub, sig, standing: useStanding};
      if (typed) body.typed_amount = Math.round(parseFloat(typed.value.replace(/[^\d.,]/g, "").replace(/\s/g, "").replace(",", ".")) * 100);
      if (answers) {
        const a = {};
        for (const [k, [y, n]] of Object.entries(answers)) if (y.checked || n.checked) a[k] = y.checked;
        if (Object.keys(a).length) body.answers = a;
      }
      if (pin && pin.value) body.pin = pin.value;
      status.textContent = "signed on this device";
      const items = await api(`/api/cards/${d.pending_id}/confirm`, body);
      add(items); refresh();
      if (items.some((x) => x.type === "error" && x.data && x.data.pending_id === d.pending_id)) unlock();
    } catch (e) { status.textContent = e.message; unlock(); }
  };
  if (c.hold_to_confirm) {
    let t = null;
    const bar = yes.querySelector(".bar");
    const start = () => { if (yes.disabled) return; bar.style.transition = "width 1.5s linear"; bar.style.width = "100%"; t = setTimeout(go, 1500); };
    const stop = () => { clearTimeout(t); bar.style.transition = "none"; bar.style.width = "0"; };
    yes.addEventListener("mousedown", start); yes.addEventListener("touchstart", start);
    ["mouseup", "mouseleave", "touchend"].forEach((ev) => yes.addEventListener(ev, stop));
  } else yes.addEventListener("click", go);
  el.append(h("div", {class: "actions"}, yes, no, status));
  el.append(h("div", {class: "hash"}, `mandate ${c.mandate_hash.slice(0, 16)}…`));
  el.append(costLine(d), steps(d));
  return el;
}

function domainChecks(d) {
  const dc = d.domain_check || {};
  const row = (ok, text) => h("li", {class: ok ? "ok" : "bad"}, (ok ? "✔ " : "✖ ") + text);
  return h("ul", {class: "checks"},
    row(dc.matches, `${dc.domain} is the official domain of ${d.merchant} in the bank's directory`),
    row(!dc.imitates, dc.imitates ? `imitates ${dc.imitates}` : "not a look-alike of another merchant"),
    row(dc.certificate && dc.certificate.valid, dc.certificate ? `certificate for ${dc.certificate.subject}, issued by ${dc.certificate.issuer}` : "no valid certificate"));
}

function secureForm(it) {
  const d = it.data;
  const el = h("div", {class: "secure"});
  el.append(h("div", {class: "secure-head"}, "🔒 Bank protected form", h("span", {class: "meta"}, " · the assistant never sees what you type")));
  el.append(h("div", {}, it.text), domainChecks(d));
  if (d.error) el.append(h("div", {class: "alert"}, d.error));
  const status = h("span", {class: "meta"});
  if (it.type === "login") {
    const login = h("input", {placeholder: `login on ${d.domain}`, autocomplete: "off", name: "login"});
    const pass = h("input", {type: "password", placeholder: "password", autocomplete: "off", name: "password"});
    const remember = h("input", {type: "checkbox", name: "remember"});
    const btn = h("button", {onclick: async () => {
      btn.disabled = true;
      await run(api(`/api/cancellations/${d.cancellation}/login`, {login: login.value, password: pass.value, remember: remember.checked}));
    }}, "Sign in");
    el.append(h("div", {class: "secure-fields"}, login, pass),
      h("label", {class: "standing"}, remember, `Remember for ${d.domain} in the bank vault (otherwise used once)`),
      h("div", {class: "actions"}, btn, status));
    if (d.demo_hint) el.append(h("div", {class: "meta"}, "Demo account: ",
      h("button", {class: "ghost-dark", onclick: () => { login.value = d.demo_hint[0]; pass.value = d.demo_hint[1]; }}, "fill demo login")));
  } else {
    const code = h("input", {placeholder: "code from SMS", inputmode: "numeric", autocomplete: "one-time-code", name: "code", maxlength: "8"});
    const btn = h("button", {onclick: async () => { btn.disabled = true; await run(api(`/api/cancellations/${d.cancellation}/code`, {code: code.value})); }}, "Send code");
    el.append(h("div", {class: "secure-fields"}, code), h("div", {class: "actions"}, btn, status));
  }
  return el;
}

function table(rows, cols) {
  return h("table", {}, h("tr", {}, cols.map(([, t]) => h("th", {}, t))),
    rows.map((r) => h("tr", {}, cols.map(([k]) => h("td", {}, typeof k === "function" ? k(r) : r[k])))));
}

function render(it) {
  const d = it.data || {};
  switch (it.type) {
    case "user": return h("div", {class: "msg user"}, it.text);
    case "card": return renderCard(it);
    case "receipt": return h("div", {class: "msg receipt" + (d.notify_only ? " notify" : "")},
      (d.notify_only ? "🔔 " : "✔ ") + it.text,
      d.verified ? h("ul", {class: "checks"}, d.verified.map((x) => h("li", {class: "ok"}, "✔ " + x))) : null,
      d.reference ? h("div", {class: "meta"}, `ref ${d.reference}`) : null, costLine(d), steps(d));
    case "login": case "code": return secureForm(it);
    case "phone": return h("div", {class: "msg phone"}, "📱 " + it.text, h("div", {class: "meta"}, `to ${d.to} · on your phone, not in the bank`));
    case "denied": return h("div", {class: "msg denied"}, "⛔ " + it.text, steps(d));
    case "error": return h("div", {class: "msg error"}, it.text);
    case "gap": return h("div", {class: "msg notice"}, "⚠ " + it.text, optionButtons(d.options));
    case "call": return h("div", {class: "msg phone"}, it.text, h("div", {class: "meta"}, d.note + (d.spoofed ? " · number looks spoofed" : "")),
      h("div", {class: "opts"}, h("button", {class: "secondary", onclick: async (e) => { e.target.disabled = true; await run(api("/api/demo/call", {active: false})); }}, "Hang up")));
    case "held": return h("div", {class: "msg notice"}, "⏸ " + it.text, optionButtons(d.options),
      d.case ? h("div", {class: "meta"}, `Case ${d.case.id}: only a person at the bank may ${d.case.human_only.join(", ")}.`) : null,
      d.case ? h("div", {class: "opts"},
        h("button", {class: "secondary", onclick: async (e) => { e.target.disabled = true; await run(api(`/api/holds/${d.hold}/analyst`, {approve: false, note: "demo analyst"})); }}, "Analyst: stop it"),
        h("button", {class: "secondary", onclick: async (e) => { e.target.disabled = true; await run(api(`/api/holds/${d.hold}/analyst`, {approve: true, note: "demo analyst"})); }}, "Analyst: release")) : null);
    case "trusted": return h("div", {class: "msg phone"}, "📱 " + it.text, h("div", {class: "meta"}, `to ${d.phone}`));
    case "trial": return h("div", {class: "msg notice"}, "⏰ " + it.text, optionButtons(d.options));
    case "options": return h("div", {class: "msg notice"}, it.text, optionButtons(d.options));
    case "budget": return h("div", {class: "msg"}, it.text,
      table(d.envelopes, [["name", "Envelope"], ["kind", "Type"], ["planned", "Planned"], ["average", "Average"]]),
      h("div", {class: "meta"}, d.calc),
      h("div", {class: "opts"}, h("button", {onclick: async (e) => { e.target.disabled = true; await run(api("/api/budget/apply", {})); }},
        d.applied ? "Re-open envelopes until payday" : "Open these envelopes until payday")));
    case "safe": return h("div", {class: "msg"}, it.text, h("div", {class: "meta"}, d.calc),
      Object.keys(d.per_envelope || {}).length ? table(Object.entries(d.per_envelope).map(([k, v]) => ({k, v})), [["k", "Envelope"], ["v", "Per day"]]) : null);
    case "afford": return h("div", {class: "msg"}, it.text,
      d.sources && d.sources.length ? table(d.sources.map(([k, v]) => ({k, v})), [["k", "From"], ["v", "Amount"]]) : null,
      h("div", {class: "meta"}, "Guardian only plans purchases; it never buys on its own."));
    case "leaks": return h("div", {class: "msg"}, it.text,
      table(d.leaks, [["name", "Service"], ["kind", "Problem"], ["monthly", "Per month"], ["note", "Why"],
        [(r) => r.option ? h("button", {class: "secondary", onclick: async (e) => { e.target.disabled = true; await run(api(`/api/options/${r.option}`, {})); }}, "Block") : (r.counted ? "" : "info"), ""]]),
      h("div", {class: "meta"}, d.note + " " + d.calc));
    case "double": return h("div", {class: "msg"}, it.text,
      h("div", {class: "meta"}, `${d.first.id} at ${d.first.ts} · ${d.second.id} at ${d.second.ts} · card ${d.card}`));
    case "bills": return h("div", {class: "msg"}, it.text,
      table(d.bills, [["subject", "Bill"], ["amount", "Amount"], ["due", "Due"],
        [(r) => r.verdict === "verified" ? "✔ matches bank records" : "⚠ " + r.verdict, "Check"]]));
    case "case": return h("div", {class: "msg case"}, "⛔ " + it.text,
      h("ul", {}, (d.reasons || []).map((r) => h("li", {}, r))),
      d.case ? h("div", {class: "meta"}, `Case ${d.case.id} sent to the bank's fraud analysts. ${d.case.recommendation}`) : null);
    case "goal": return h("div", {class: "msg"}, "🎯 " + it.text,
      table(d.months, [["month", "Month"], ["left", "Left after bills, envelopes and goal"], [(r) => r.ok ? "✔" : "⚠", ""]]),
      d.cuts.length ? h("div", {class: "meta"}, "From envelopes: " + d.cuts.map((c) => `${c.name} ${c.amount}`).join(", ")) : null,
      h("div", {class: "meta"}, d.calc + (d.untouchable ? ` · not touched: ${d.untouchable}` : "")),
      optionButtons(d.options));
    case "salary": return h("div", {class: "msg"}, "💰 " + it.text,
      table(d.rows, [["name", "Where"], ["amount", "Amount"], [(r) => r.planned ? `planned ${r.planned}` : "", ""]]),
      (d.notes || []).length ? h("div", {class: "meta"}, d.notes.join("; ")) : null);
    case "boost": return h("div", {class: "msg notice"}, "🎯 " + it.text, h("div", {class: "meta"}, d.calc), optionButtons(d.options));
    case "review": return h("div", {class: "msg"}, it.text,
      table(d.rows, [["name", "Envelope"], ["planned", "Planned"], ["actual", "Spent"], ["diff", "Difference"]]),
      d.anomalies.length ? h("ul", {}, d.anomalies.map((a) => h("li", {}, "⚠ " + a))) : null,
      d.price_changes.length ? h("div", {class: "meta"}, "Price changes: " + d.price_changes.join("; ")) : null,
      d.suggestions.length ? h("div", {class: "meta"}, "Suggested next plan: " + d.suggestions.map((x) => `${x.name} ${x.amount}`).join(", ")) : null);
    case "quality": return h("div", {class: "msg"}, it.text,
      table(Object.entries(d.forecast.mape).map(([k, v]) => ({k: d.names[k] || k, v: v + "%"})), [["k", "Envelope"], ["v", "Average error"]]),
      h("div", {class: "meta"}, `Trained on ${d.forecast.train[0]}…${d.forecast.train.at(-1)}, tested on ${d.forecast.test.join(", ")}; gap back-test at ${d.gaps.probes} past dates`));
    case "permissions": return h("div", {class: "msg"}, it.text,
      h("ul", {}, (d.mandates || []).map((m) => h("li", {}, m.can.join("; ")))));
    default: return h("div", {class: "msg " + it.type}, it.text,
      d.funding ? table(d.funding, [["name", "Envelope"], ["amount", "Funded"]]) : null);
  }
}

function add(items) {
  for (const it of items) {
    if (it.seq <= lastSeq) continue;
    lastSeq = it.seq;
    feed.append(render(it));
  }
  feed.scrollTop = feed.scrollHeight;
}

async function run(p) {
  try { add(await p); } catch (e) { add([{seq: lastSeq + 0.001, type: "error", text: e.message, data: {}}]); }
  refresh();
}

// ------------------------------------------------------------------ side panels

function fill(el, ...kids) {
  el.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
}

async function refresh() {
  const s = await api("/api/state");
  $("#now").textContent = new Date(s.now).toLocaleString("en-GB", {timeZone: "Europe/Moscow", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit"}) + " MSK";
  const b = $("#tab-budget");
  fill(b, 
    h("div", {class: "meta"}, "Safe to spend today"),
    h("div", {class: "big", "data-testid": "safe"}, s.safe_to_spend.per_day),
    h("div", {class: "meta"}, `${s.safe_to_spend.days} days to payday ${s.safe_to_spend.payday}`),
    s.gap.length ? h("div", {class: "alert"}, `Cash gap on ${s.gap[0].date}: ${s.gap[0].event}, ${s.gap[0].shortfall} missing`) : null,
    h("h4", {}, "Accounts"),
    ...s.accounts.map((a) => h("div", {class: "env"}, h("div", {class: "row"}, h("span", {}, a.name), h("b", {}, a.label)))),
    h("h4", {}, "Envelopes"),
    s.envelopes.length ? null : h("div", {class: "meta"}, "No envelopes yet. Ask: Make me a budget"),
    ...s.envelopes.map((e) => {
      const max = Math.max(...s.envelopes.map((x) => x.balance), 1);
      return h("div", {class: "env"}, h("div", {class: "row"}, h("span", {}, e.name), h("span", {}, e.label)),
        h("div", {class: "track"}, h("div", {class: "fill", style: `width:${Math.round(100 * e.balance / max)}%`})));
    }),
    h("h4", {}, "Models"),
    h("div", {class: "meta"}, `mode ${s.llm.mode}; spent ${s.llm.spent_total_rub} ₽ of ${s.llm.cap_rub} ₽; critic calls ${s.critic.calls}, cache hits ${s.critic.cache_hits}`),
    h("div", {class: "meta"}, "Trust: " + Object.entries(s.trust).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(", ")));
  const p = $("#tab-permissions");
  fill(p, h("div", {class: "meta"}, "Standing permissions you signed. Revoking takes effect at once."),
    ...(s.mandates.length ? s.mandates.map((m) => h("div", {class: "mandate"},
      h("ul", {}, m.can.map((x) => h("li", {}, x))),
      h("div", {class: "meta"}, `used ${m.uses} times, ${m.spent} · stage ${m.stage} · ${m.hash.slice(0, 12)}…`),
      h("button", {class: "danger", onclick: () => run(api(`/api/mandates/${m.id}/revoke`, {}))}, "Revoke")))
      : [h("p", {}, "None. Guardian asks you before every action.")]),
    s.vault.length ? h("h4", {}, "Saved sign-ins (bank vault)") : null,
    ...s.vault.map((v) => h("div", {class: "mandate"}, `${v.name} · only on ${v.domain} · used ${v.uses} times`,
      h("button", {class: "danger", onclick: () => run(api("/api/vault/forget", {name: v.name}))}, "Forget"))),
    s.templates.length ? h("h4", {}, "Payment templates") : null,
    ...s.templates.map((t) => h("div", {class: "meta"}, `${t.payee}: ${t.amount}, used ${t.uses} times`)));
  fill($("#tab-inbox"), h("div", {class: "meta"}, "Guardian screens every message on arrival."),
    ...s.inbox.map((d) => h("div", {class: "doc"}, h("div", {}, d.flagged ? h("span", {class: "flag"}, "⚠ suspicious ") : null,
      d.subject, d.paid ? " ✔ paid" : ""), h("div", {class: "meta"}, `${d.date} · ${d.sender}`))));
  const j = await api("/api/journal");
  fill($("#tab-journal"), 
    h("div", {class: j.ok ? "meta" : "alert"}, j.ok ? `Signed hash chain verified: ${j.checked} entries, head ${j.head}…` : "Journal verification FAILED"),
    ...j.entries.map((e) => h("div", {class: "jr"}, `#${e.seq} ${e.actor} ${e.event}${e.stage ? " · stage " + e.stage : ""}`)));
}

// ------------------------------------------------------------------ wiring

const CHIPS = ["Pay for Melodify", "Cancel the 399 ₽ subscription", "Find leaks in my subscriptions", "Was I charged twice?", "Pay my bills",
  "Make me a budget", "How much can I spend today?", "Can I afford headphones for 15 000?",
  "Save 60 000 for a vacation by July, don't touch the gym",
  "Transfer 350 000 ₽ from my savings to safe account 40817810500001234567", "Month review", "How accurate is your forecast?", "What can you do?"];

function init() {
  $("#chips").append(...CHIPS.map((t) => h("button", {type: "button", onclick: () => send(t)}, t)));
  $("#ask").addEventListener("submit", (e) => { e.preventDefault(); const t = $("#text").value.trim(); if (t) send(t); });
  $("#wait15").addEventListener("click", () => run(api("/api/time/wait", {minutes: 15})));
  $("#scamcall").addEventListener("click", () => run(api("/api/demo/call", {active: true, spoofed: true})));
  document.querySelectorAll("[data-advance]").forEach((b) => b.addEventListener("click",
    () => run(api("/api/time/advance", {days: +b.dataset.advance}))));
  document.querySelectorAll("aside nav button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("aside nav button, .tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); $(`#tab-${b.dataset.tab}`).classList.add("active");
  }));
}

function send(text) {
  $("#text").value = "";
  return run(api("/api/chat", {text}));
}

function stream() {
  const es = new EventSource(`/api/events?since=${lastSeq}`);
  es.addEventListener("item", (e) => add([JSON.parse(e.data)]));
  es.addEventListener("state", () => refresh());
  es.onerror = () => { es.close(); setTimeout(stream, 2000); };
}

(async () => {
  init();
  await loadDevice().catch((e) => { $("#device").textContent = "device key unavailable: " + e.message; });
  add(await api("/api/items?since=0"));
  await refresh();
  stream();
})();
