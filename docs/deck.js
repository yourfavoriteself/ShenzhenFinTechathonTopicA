// Presentation (≤ 10 minutes) with speaker notes.  node docs/deck.js  ->  docs/presentation.pptx
// Numbers are read from evidence/*.json so the slides match the reports.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const root = path.resolve(__dirname, "..");
const ev = (f) => JSON.parse(fs.readFileSync(path.join(root, "evidence", f), "utf8"));
const rt = ev("redteam.json"), evl = ev("eval_report.json"), cost = ev("costs.json"), demo = ev("demo_summary.json");
const bud = ev("budget_report.json"), graph = ev("risk_graph.json");
const media = (f) => path.join(root, "docs", "media", f);
const num = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
const rub = (kop) => num(Math.floor(kop / 100)) + "." + String(kop % 100).padStart(2, "0") + " ₽";

const C = {dark: "064E3B", mid: "065F46", accent: "10B981", soft: "ECFDF5", ink: "1F2937", grey: "6B7280", line: "D1D5DB",
  red: "B91C1C", amber: "B45309", white: "FFFFFF"};
const HF = "Calibri", BF = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.title = "Guardian: a bank-side safety kernel for AI payment agents";

function slide(title, notes, {bg = C.white} = {}) {
  const s = pres.addSlide();
  s.background = {color: bg};
  if (title) s.addText(title, {x: 0.5, y: 0.3, w: 9, h: 0.7, fontFace: HF, fontSize: title.length > 52 ? 24 : 28, bold: true, color: C.dark, margin: 0});
  if (notes) s.addNotes(notes);
  return s;
}

function stat(s, x, y, w, big, small, color = C.dark) {
  s.addShape(pres.shapes.RECTANGLE, {x, y, w, h: 1.35, fill: {color: C.soft}, line: {color: C.soft}});
  s.addText(big, {x, y: y + 0.12, w, h: 0.7, fontFace: HF, fontSize: 32, bold: true, color, align: "center", margin: 0});
  s.addText(small, {x: x + 0.1, y: y + 0.8, w: w - 0.2, h: 0.5, fontFace: BF, fontSize: 11, color: C.ink, align: "center", valign: "top", margin: 0});
}

function shot(s, file, x, y, w, h) {
  s.addImage({path: media(file), x, y, w, h});
  s.addShape(pres.shapes.RECTANGLE, {x, y, w, h, fill: {type: "none"}, line: {color: C.line, width: 0.75}});
}

const st = rt.static, ad = rt.adaptive, lg = rt.legacy_prototype, rr = rt.retention;

// 1 Title
{
  const s = slide(null, "Hello. We are the Guardian team. Our topic is A: an AI agent that pays, cancels and budgets for a bank client. "
    + "In ten minutes: the problem, the idea, a month of the demo, how the kernel works, the security results, compliance, cost and what is next.", {bg: C.dark});
  s.addText("Guardian", {x: 0.6, y: 1.3, w: 8.8, h: 1.1, fontFace: HF, fontSize: 60, bold: true, color: C.white, margin: 0});
  s.addText("A bank-side safety kernel for AI payment agents", {x: 0.6, y: 2.4, w: 8.8, h: 0.6, fontFace: HF, fontSize: 24, color: "A7F3D0", margin: 0});
  s.addText("Agents propose. The kernel decides, checks and records.", {x: 0.6, y: 3.2, w: 8.8, h: 0.5, fontFace: BF, fontSize: 16, italic: true, color: C.white, margin: 0});
  s.addText("Shenzhen FinTechathon 2026 · International Track · Topic A", {x: 0.6, y: 4.6, w: 8.8, h: 0.4, fontFace: BF, fontSize: 13, color: "A7F3D0", margin: 0});
}

// 2 Problem
{
  const s = slide("Letting an AI agent touch money is a new attack surface",
    "Agents can now pay. That brings three new problems. First, intent: a model can get the payee or the amount wrong, and nobody can prove what the client actually approved. "
    + "Second, manipulation: a fake bill, a hidden instruction for AI agents in a document, or a scam call can turn the agent against its owner. "
    + "Third, accountability: when something goes wrong, there is no record of who authorised what. "
    + `To make it concrete: we ran our ${st.attacks} attacks against an agent without our kernel. ${st.without_kernel.succeeded} succeeded and it lost ${rub(st.without_kernel.lost_kop)}.`);
  const items = [
    ["Intent", "A model can pick the wrong payee or amount. The client cannot prove what they approved."],
    ["Manipulation", "Fake bills, hidden instructions for AI agents, look-alike merchants, 'safe account' scam calls."],
    ["Accountability", "Regulators ask: who authorised this payment, which model decided, can it be rolled back?"],
  ];
  items.forEach(([h, t], i) => {
    const y = 1.3 + i * 1.1;
    s.addShape(pres.shapes.OVAL, {x: 0.6, y: y + 0.08, w: 0.5, h: 0.5, fill: {color: C.accent}, line: {color: C.accent}});
    s.addText(String(i + 1), {x: 0.6, y: y + 0.08, w: 0.5, h: 0.5, fontFace: HF, fontSize: 18, bold: true, color: C.white, align: "center", valign: "middle", margin: 0});
    s.addText(h, {x: 1.3, y, w: 4.2, h: 0.35, fontFace: HF, fontSize: 18, bold: true, color: C.ink, margin: 0});
    s.addText(t, {x: 1.3, y: y + 0.38, w: 4.4, h: 0.6, fontFace: BF, fontSize: 13, color: C.grey, margin: 0});
  });
  s.addShape(pres.shapes.RECTANGLE, {x: 6.1, y: 1.3, w: 3.4, h: 3.2, fill: {color: "FEF2F2"}, line: {color: "FEF2F2"}});
  s.addText("Same attacks, agent without the kernel", {x: 6.3, y: 1.45, w: 3.0, h: 0.6, fontFace: BF, fontSize: 13, color: C.ink, margin: 0});
  s.addText(`${st.without_kernel.succeeded} / ${st.attacks}`, {x: 6.3, y: 2.05, w: 3.0, h: 0.8, fontFace: HF, fontSize: 44, bold: true, color: C.red, margin: 0});
  s.addText("attacks succeeded", {x: 6.3, y: 2.8, w: 3.0, h: 0.35, fontFace: BF, fontSize: 13, color: C.ink, margin: 0});
  s.addText(rub(st.without_kernel.lost_kop), {x: 6.3, y: 3.3, w: 3.0, h: 0.6, fontFace: HF, fontSize: 26, bold: true, color: C.red, margin: 0});
  s.addText("lost from the sandbox client", {x: 6.3, y: 3.9, w: 3.0, h: 0.35, fontFace: BF, fontSize: 13, color: C.ink, margin: 0});
}

// 3 Idea
{
  const s = slide("The idea: models never hold the money",
    "Our answer is to split the system in two. Agents and language models only propose an intent. "
    + "A deterministic kernel inside the bank decides, executes through the bank's own executor, checks the result in the bank and writes a signed journal. "
    + "That gives the client three promises. What you see is what you sign: the card is rendered by the kernel and the device key signs its hash. "
    + "Nothing outside the mandate: every payment fits limits the client set. And every result is verified and recorded.");
  const cols = [
    ["What you see is what you sign", "The kernel renders every card. The client's device key (Ed25519 / P-256, WebCrypto) signs its hash; the kernel countersigns."],
    ["Nothing outside the mandate", "Payee, amount range, period, money source, expiry. Agents get single-use grants that only narrow."],
    ["Verified, then recorded", "After execution the kernel checks the bank itself, not the agent's word. Every step goes into a signed hash chain."],
  ];
  cols.forEach(([h, t], i) => {
    const x = 0.5 + i * 3.1;
    s.addShape(pres.shapes.RECTANGLE, {x, y: 1.35, w: 2.9, h: 2.9, fill: {color: C.soft}, line: {color: C.soft}});
    s.addText(h, {x: x + 0.2, y: 1.5, w: 2.5, h: 0.8, fontFace: HF, fontSize: 17, bold: true, color: C.dark, valign: "top", margin: 0});
    s.addText(t, {x: x + 0.2, y: 2.35, w: 2.5, h: 1.8, fontFace: BF, fontSize: 13, color: C.ink, valign: "top", margin: 0});
  });
  s.addText("Agents propose  →  kernel decides  →  bank executes  →  kernel verifies  →  journal records",
    {x: 0.5, y: 4.55, w: 9, h: 0.45, fontFace: HF, fontSize: 15, bold: true, color: C.mid, align: "center", margin: 0});
}

// 4 Month for you
{
  const m = demo.month;
  const s = slide("Demo: one month for one client, scenarios C1 to C10",
    "The demo is one month in a sandbox bank with synthetic data. Ten scenarios. Pay a subscription and allow it monthly; cancel one through the merchant's API; "
    + "find leaks; a double charge; bills where one is fake; a free trial turning paid; a budget with a goal; a 'safe account' scam call; and a risky payee on the transfer graph. "
    + `Over the month Guardian saved ${m.saved_per_month} a month, did ${m.done} things, asked ${m.asked} times and stopped ${m.stopped}. `
    + `The whole run is in the evidence folder: ${demo.journal.entries} signed journal entries, chain verified.`);
  const sc = [
    ["C1", "Pay, allow monthly", "standing, ≤ 344 ₽"], ["C2", "Cancel a subscription", "merchant API + card block"],
    ["C3", "Find leaks", "847 ₽ a month"], ["C4", "Double charge", "dispute opened"],
    ["C5", "Pay bills", "checked against bank records"], ["C6", "Fake bill", "stopped, case to analysts"],
    ["C7", "Trial turns paid", "cancelled before charge"], ["C8", "Budget and goal", "envelopes, safe-to-spend"],
    ["C9", "Scam call", "pause → analyst hold"], ["C10", "Risky payee", "transfer-graph model"],
  ];
  sc.forEach(([id, t, r], i) => {
    const col = i % 5, row = Math.floor(i / 5);
    const x = 0.5 + col * 1.82, y = 1.3 + row * 1.45;
    s.addShape(pres.shapes.RECTANGLE, {x, y, w: 1.7, h: 1.3, fill: {color: C.soft}, line: {color: C.soft}});
    s.addText(id, {x: x + 0.12, y: y + 0.08, w: 1.5, h: 0.35, fontFace: HF, fontSize: 16, bold: true, color: C.accent, margin: 0});
    s.addText(t, {x: x + 0.12, y: y + 0.42, w: 1.5, h: 0.4, fontFace: HF, fontSize: 12.5, bold: true, color: C.ink, margin: 0});
    s.addText(r, {x: x + 0.12, y: y + 0.82, w: 1.5, h: 0.4, fontFace: BF, fontSize: 10.5, color: C.grey, valign: "top", margin: 0});
  });
  const k = [[m.saved_per_month, "saved a month"], [String(m.done), "done"], [String(m.asked), "asked"], [String(m.stopped), "stopped"],
    [String(demo.journal.entries), "journal entries, verified"]];
  k.forEach(([b, t], i) => {
    s.addText([{text: b + " ", options: {bold: true, color: C.dark, fontSize: 18}}, {text: t, options: {color: C.grey, fontSize: 12}}],
      {x: 0.5 + i * 1.82, y: 4.3, w: 1.8, h: 0.6, fontFace: HF, margin: 0, valign: "middle"});
  });
}

// 5 Screens
{
  const s = slide("What the client sees",
    "Here is the real interface. On the left, a card. The kernel renders it, every field has its source, and you hold to sign with the device key. "
    + "On the right, the fake bill. It imitates Mosenergo, the account is not Mosenergo's, it uses pressure and has a hidden instruction for AI agents, and it is linked to an earlier phishing email. "
    + "The scam call, where the ladder goes from explain to ask to a pause and then holds the money for a person at the bank, is in the demo video.");
  const items = [["01_card_chat.png", "A card: every field with its source, signed with the device key"],
    ["06_fake_bill_chat.png", "A fake bill: stopped with reasons, case sent to analysts"]];
  items.forEach(([f, t], i) => {
    const x = 0.75 + i * 4.4, w = 3.85;
    shot(s, f, x, 1.05, w, w * 740 / 725);
    s.addText(t, {x: x - 0.2, y: 1.05 + w * 740 / 725 + 0.06, w: w + 0.4, h: 0.4, fontFace: BF, fontSize: 12, color: C.ink, align: "center", margin: 0});
  });
}

// 6 Architecture
{
  const s = slide("Architecture",
    "The architecture. On top, the client's chat and device key. In the middle, agents with passports: the conversation agent, the budget and document agents, the critic. "
    + "They call models through one gateway with a three-level ladder. Everything they propose goes to the kernel. The kernel holds mandates, the vault, the journal and the only path to the bank's executor. "
    + "Merchants are reached through their API in the sandbox. The journal is in PostgreSQL with row-level security, append-only, and checkpoints are anchored by a smart contract.");
  s.addImage({path: path.join(root, "docs/diagrams/architecture.png"), x: 0.5, y: 1.1, w: 9, h: 9 * 1069 / 2119});
}

// 7 Kernel pipeline
{
  const s = slide("Every intent goes through the same kernel pipeline",
    "Every intent, whoever proposes it, goes through the same pipeline. Passport: is this agent allowed to ask. Schema. Mandate: does it fit what the client signed. "
    + "Provenance: each value is labelled CLIENT, BANK or UNTRUSTED, and untrusted values cannot set a payee or an amount. References, policy and risk, including the transfer-graph model. "
    + "A critic that only sees the client's own words. A shadow run in a copy of the bank. Only then a single-use grant executes, and the kernel verifies the result in the bank.");
  const steps = ["Passport", "Schema", "Mandate", "Provenance", "References", "Policy", "Risk", "Critic", "Shadow run", "Execute", "Verify"];
  steps.forEach((t, i) => {
    const col = i % 6, row = Math.floor(i / 6);
    const x = 0.5 + col * 1.5, y = 1.35 + row * 0.95;
    s.addShape(pres.shapes.CHEVRON, {x, y, w: 1.45, h: 0.7, fill: {color: i >= 9 ? C.dark : C.accent}, line: {color: C.white}});
    s.addText(t, {x: x + 0.15, y, w: 1.2, h: 0.7, fontFace: HF, fontSize: 11.5, bold: true, color: C.white, align: "center", valign: "middle", margin: 0});
  });
  const notes = [
    ["Provenance", "CLIENT · BANK · UNTRUSTED. A value from a bill or a web page can never set the payee or the amount."],
    ["Critic", "A second model sees only the client's words and the card, not the documents an attacker controls."],
    ["Shadow run", "The payment is rehearsed in a copy of the bank; if money moves differently, it is denied."],
    ["Trust levels 0, 1, 2", "Level 1 limit 1 000 ₽; promotion after 5 verified results in a row. Autonomy stages 1 and 2, stage 3 not offered."],
  ];
  notes.forEach(([h, t], i) => {
    const x = 0.5 + (i % 2) * 4.6, y = 3.4 + Math.floor(i / 2) * 0.95;
    s.addText([{text: h + "  ", options: {bold: true, color: C.dark}}, {text: t, options: {color: C.ink}}],
      {x, y, w: 4.4, h: 0.85, fontFace: BF, fontSize: 12, valign: "top", margin: 0});
  });
}

// 8 Budget and graph
{
  const leaks = bud.leaks, fc = bud.budget_forecast, gaps = bud.cash_gaps;
  const s = slide("Budget and payee risk: measured, not claimed",
    "Two analytical parts, each measured on held-out data. The budget agent found all five planted leaks and the double charge. "
    + "The spending forecast has a weighted error of about 27 percent on held-out months, and the cash-gap back-test found the real gap with no false alarms. "
    + "The payee risk model is gradient boosting over 14 features of the bank's transfer graph. "
    + "On held-out synthetic graphs it scores ROC-AUC 1.0, precision 0.993 and recall 0.944. These are synthetic graphs from our own generator; real bank data will score lower. "
    + "The point is that the pipeline is in place and the model only adds a reason to ask: it never moves money by itself.");
  s.addText("C8 budget", {x: 0.5, y: 1.2, w: 4.4, h: 0.4, fontFace: HF, fontSize: 17, bold: true, color: C.mid, margin: 0});
  s.addText("C10 payee risk on the transfer graph", {x: 5.1, y: 1.2, w: 4.4, h: 0.4, fontFace: HF, fontSize: 17, bold: true, color: C.mid, margin: 0});
  stat(s, 0.5, 1.7, 2.1, `${leaks.found}/${leaks.planted}`, `planted leaks found, ${leaks.monthly_total} a month`);
  stat(s, 2.8, 1.7, 2.1, `${fc.overall_wape_pct}%`, "spending forecast error (WAPE), held-out months");
  stat(s, 0.5, 3.2, 2.1, `${gaps.caught_in_time}/${gaps.actual.length}`, `cash gap caught in time, ${gaps.false_alarms} false alarms in ${gaps.probes} probes`);
  stat(s, 2.8, 3.2, 2.1, `${bud.double_charge.found}/${bud.double_charge.planted}`, "double charge on the same card found");
  stat(s, 5.1, 1.7, 2.1, graph.roc_auc.toFixed(2), "ROC-AUC on held-out graphs");
  stat(s, 7.4, 1.7, 2.1, graph.precision.toFixed(3), "precision at threshold 0.7");
  stat(s, 5.1, 3.2, 2.1, graph.recall.toFixed(3), "recall over mule schemes");
  stat(s, 7.4, 3.2, 2.1, String(graph.false_positives_on_employers_merchants_landlords), "false alarms on employers, merchants, landlords");
  s.addText("Synthetic graphs from our generator; real data will score lower. The model can only add a reason to ask.",
    {x: 5.1, y: 4.65, w: 4.4, h: 0.5, fontFace: BF, fontSize: 10.5, italic: true, color: C.grey, margin: 0});
}

// 9 Security
{
  const s = slide("Red-team: what an attacker gets",
    `We attacked the kernel four ways. ${st.attacks} static attacks: fake bills, injected instructions, swapped payees, look-alike merchants, replayed signatures, over-limit requests. All ${st.blocked} stopped. `
    + `${rr.attacks} attacks that try to plant something in the assistant's memory for later: all stopped. `
    + `An adaptive attacker that learns from the kernel's answers, ${ad.rounds} rounds, ${ad.with_kernel.attempts} attempts: zero payments outside the mandate. `
    + `And the earlier prototype's suite: ${lg.prevented} of ${lg.attacks} prevented, one bounded by the limit. Without the kernel the same static set lost ${rub(st.without_kernel.lost_kop)}.`);
  stat(s, 0.5, 1.3, 2.1, `${st.blocked}/${st.attacks}`, "static attacks stopped");
  stat(s, 2.8, 1.3, 2.1, `${rr.blocked}/${rr.attacks}`, "memory-planting attacks stopped");
  stat(s, 5.1, 1.3, 2.1, `${ad.with_kernel.out_of_mandate_successes}/${ad.with_kernel.attempts}`, `adaptive attacker's tries outside the mandate`);
  stat(s, 7.4, 1.3, 2.1, `${lg.prevented}/${lg.attacks}`, `earlier prototype's suite, ${lg.bounded} bounded by the limit`);
  s.addText("Without the kernel", {x: 0.5, y: 2.95, w: 4, h: 0.4, fontFace: HF, fontSize: 16, bold: true, color: C.ink, margin: 0});
  s.addText([{text: `${st.without_kernel.succeeded}/${st.attacks} succeeded · `, options: {color: C.red, bold: true}},
    {text: rub(st.without_kernel.lost_kop) + " lost", options: {color: C.red, bold: true}}],
  {x: 0.5, y: 3.35, w: 4.4, h: 0.45, fontFace: HF, fontSize: 18, margin: 0});
  s.addText("Known risks are listed openly in the security report (K1 to K14): a compromised client device, a colluding analyst, synthetic training data, no SD-JWT yet.",
    {x: 0.5, y: 3.95, w: 4.4, h: 0.9, fontFace: BF, fontSize: 12, color: C.grey, margin: 0});
  const by = {};
  rt.attacks.forEach((a) => { by[a.outcome] = (by[a.outcome] || 0) + 1; });
  const data = [{name: "Attacks", labels: Object.keys(by), values: Object.values(by)}];
  s.addChart(pres.charts.BAR, data, {x: 5.1, y: 2.85, w: 4.4, h: 2.2, barDir: "bar", chartColors: [C.accent],
    catAxisLabelFontSize: 11, valAxisLabelFontSize: 10, valGridLine: {color: "E5E7EB", size: 0.5}, catGridLine: {style: "none"},
    showValue: true, dataLabelFontSize: 10, showTitle: true, title: "How the kernel stopped the static attacks", titleFontSize: 12, titleColor: C.ink,
    showLegend: false});
}

// 10 Compliance
{
  const s = slide("Compliance: 智能体支付应用自律公约 · VI / AP2",
    "Compliance. The self-regulatory code for agent payment apps has three stages; we implement stage one and two, and we do not offer stage three. "
    + "Whoever provides the payment service is responsible: Guardian is a bank module, only the bank's executor moves money. "
    + "Know your agent: every agent has a passport checked at every step. Withdrawal of authorisation is one press. Records are immutable and anchored. "
    + "There is a human fallback and stricter rules for the elderly. And the assistant sells nothing. "
    + "On the right, our mandates map onto Mastercard Verifiable Intent and Google AP2 concepts. The full tables with where to check each line are in the security report.");
  const rows = [
    ["Three stages", "Stages 1 and 2 implemented, stage 3 not offered"],
    ["Who provides payment is responsible", "Bank module; only the bank's executor moves money"],
    ["Know your agent", "Agent passport checked at every step, in the journal"],
    ["Verify intent, clear authority", "Kernel-rendered card, device signature, critic"],
    ["Withdraw authorisation", "One press, child grants revoked too"],
    ["Immutable records", "Signed hash chain, append-only, anchored on chain"],
    ["Human fallback, emergency stop", "Analyst hold, kill switch, 24-hour reversible hold"],
    ["Elderly and minors", "Elevated protection, trusted person informed"],
    ["No inducement to spend", "No credit or upsell; offers in model text replaced"],
  ];
  const hdr = (t) => ({text: t, options: {bold: true, color: C.white, fill: {color: C.mid}}});
  s.addTable([[hdr("公约 requirement"), hdr("Guardian")], ...rows.map(([a, b]) => [a, b])],
    {x: 0.5, y: 1.2, w: 5.6, colW: [2.2, 3.4], fontFace: BF, fontSize: 10, color: C.ink, border: {pt: 0.5, color: C.line}, rowH: 0.36, margin: 0.05});
  const vi = [["Intent mandate", "Standing permission with limits"], ["Cart / payment mandate", "One-time mandate per card"],
    ["Constraints", "payees, amount range, budget, recurrence, + origins, money source"], ["Delegation", "Child grants, rights only narrow"],
    ["Signatures", "Device key + kernel countersignature"]];
  s.addTable([[hdr("VI / AP2"), hdr("Guardian")], ...vi],
    {x: 6.35, y: 1.2, w: 3.2, colW: [1.25, 1.95], fontFace: BF, fontSize: 10, color: C.ink, border: {pt: 0.5, color: C.line}, rowH: 0.36, margin: 0.05});
}

// 11 Cost
{
  const f = cost.first_month, n = cost.next_month, pj = cost.projection;
  const s = slide("Cost: most of the work needs no large model",
    "Cost. Models sit on a three-level ladder: rules and templates cost nothing, then a small model, then larger ones only when risk is high. "
    + `For one client's typical month the first month costs ${f.cost_rub.toFixed(2)} rubles in tokens; the next month ${n.cost_rub.toFixed(2)}, ${cost.saving_pct} percent less, because the repeat payment and the repeated check run from templates with no model call. `
    + `In the full demo ${demo.cost.share_level_0_1} percent of the work stays on levels zero and one. At the next-month rate a million clients cost about ${num(Math.round(pj.rub_per_month / 1000) * 1000)} rubles a month. `
    + "These are token counts on the real prompts with stub answers; a live run will replace the estimate.");
  const tasks = f.tasks.map((t) => t.task);
  s.addChart(pres.charts.BAR, [
    {name: "First month", labels: tasks, values: f.tasks.map((t) => t.cost_rub)},
    {name: "Next month", labels: tasks, values: n.tasks.map((t) => t.cost_rub)},
  ], {x: 0.4, y: 1.15, w: 5.6, h: 3.9, barDir: "bar", barGrouping: "clustered", chartColors: ["9CA3AF", C.accent],
    catAxisLabelFontSize: 10, valAxisLabelFontSize: 9, valGridLine: {color: "E5E7EB", size: 0.5}, catGridLine: {style: "none"},
    showLegend: true, legendPos: "b", legendFontSize: 10, showTitle: true, title: "₽ per task, one client", titleFontSize: 12, titleColor: C.ink,
    catAxisOrientation: "maxMin"});
  stat(s, 6.3, 1.2, 3.2, `${f.cost_rub.toFixed(2)} → ${n.cost_rub.toFixed(2)} ₽`, `one client's month, ${Math.round(cost.saving_pct)}% less in the second`);
  stat(s, 6.3, 2.65, 3.2, `${demo.cost.share_level_0_1}%`, "of work on levels 0 and 1 (rules, templates, small model)");
  stat(s, 6.3, 4.1, 3.2, `≈ ${num(Math.round(pj.rub_per_month / 1000) * 1000)} ₽`, "a month for 1 million clients");
}

// 12 Results and next
{
  const fn = evl.functional, at = evl.attacks;
  const s = slide("Results and what comes next",
    `To sum up. ${fn.passed} of ${fn.tasks} functional tasks pass in English, Russian and Chinese, ${at.stopped} of ${at.attacks} attacks in the evaluation set are stopped, and the whole repository has an automated test suite that runs in the Docker build. `
    + "Everything is reproducible from the repository in replay mode without any keys. "
    + "Next: live runs with real models, anchoring on a public test network, the payee model on real bank graphs, and SD-JWT selective disclosure for mandates. "
    + "Agents propose; the kernel decides, checks and records. Thank you.");
  stat(s, 0.5, 1.3, 2.1, `${fn.passed}/${fn.tasks}`, "functional tasks, EN / RU / ZH");
  stat(s, 2.8, 1.3, 2.1, `${at.stopped}/${at.attacks}`, "attacks in the evaluation set stopped");
  stat(s, 5.1, 1.3, 2.1, String(demo.journal.entries), "signed journal entries in the demo, chain verified");
  stat(s, 7.4, 1.3, 2.1, "0 keys", "needed to reproduce: replay mode, make test");
  s.addText("Next", {x: 0.5, y: 2.95, w: 4, h: 0.4, fontFace: HF, fontSize: 17, bold: true, color: C.mid, margin: 0});
  s.addText([
    {text: "Live runs with real models; measured cost replaces the estimate", options: {bullet: true, breakLine: true}},
    {text: "Journal anchoring on a public test network", options: {bullet: true, breakLine: true}},
    {text: "Payee model trained and checked on real bank graphs", options: {bullet: true, breakLine: true}},
    {text: "SD-JWT selective disclosure for mandates; pilot with a bank's fraud team", options: {bullet: true}},
  ], {x: 0.5, y: 3.35, w: 5.6, h: 1.6, fontFace: BF, fontSize: 13, color: C.ink, margin: 0, paraSpaceAfter: 4});
  s.addShape(pres.shapes.RECTANGLE, {x: 6.4, y: 3.0, w: 3.1, h: 1.9, fill: {color: C.dark}, line: {color: C.dark}});
  s.addText("Agents propose.\nThe kernel decides, checks and records.", {x: 6.55, y: 3.1, w: 2.8, h: 1.7, fontFace: HF, fontSize: 16, bold: true, color: C.white, valign: "middle", margin: 0});
}

pres.writeFile({fileName: path.join(root, "docs", "presentation.pptx")}).then((f) => console.log(f));
