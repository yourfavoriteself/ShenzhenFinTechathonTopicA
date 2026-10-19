"""Build docs/diagrams/architecture.drawio with exact coordinates, so no arrow crosses a block.

    make diagram
"""

from __future__ import annotations

from html import escape
from pathlib import Path

FONT = "fontFamily=Inter;"
INK, MUTED, LINE = "#1d2939", "#475467", "#d0d5dd"
GROUPS = {   # fill, stroke
    "client": ("#eef4ff", "#c7d7fe"), "agents": ("#fff6e5", "#f3d49b"), "kernel": ("#f3f0ff", "#d4ccf9"),
    "bank": ("#e8f6f3", "#b8e0d6"), "audit": ("#f4f4f5", "#d9d9de"),
}

cells: list[str] = []
_n = 0


def _id() -> str:
    global _n
    _n += 1
    return f"n{_n}"


def _label(title: str, *lines: str) -> str:
    body = "".join(f"<br><font color=\"{MUTED}\" style=\"font-size:11px\">{escape(l)}</font>" for l in lines)
    return escape(f"<b>{escape(title)}</b>{body}")


def group(key: str, title: str, x: int, y: int, w: int, h: int, dashed: bool = False) -> str:
    fill, stroke = GROUPS[key]
    i = _id()
    style = (f"rounded=1;arcSize=3;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=1.5;"
             f"verticalAlign=top;align=left;spacingLeft=14;spacingTop=8;fontSize=14;fontStyle=1;fontColor={INK};"
             f"{'dashed=1;dashPattern=6 4;' if dashed else ''}{FONT}")
    cells.append(f'<mxCell id="{i}" value="{escape(title)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i


def box(title: str, lines: tuple[str, ...], x: int, y: int, w: int, h: int, *, fill: str = "#ffffff",
        stroke: str = LINE, size: int = 12) -> str:
    i = _id()
    style = (f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize={size};"
             f"fontColor={INK};spacing=6;{FONT}")
    cells.append(f'<mxCell id="{i}" value="{_label(title, *lines)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i


def bar(text: str, x: int, y: int, w: int, h: int = 34, *, fill: str = "#ffffff", stroke: str = LINE,
        bold: bool = False) -> str:
    i = _id()
    style = (f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize=12;"
             f"fontColor={INK};{'fontStyle=1;' if bold else ''}{FONT}")
    cells.append(f'<mxCell id="{i}" value="{escape(text)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i


def edge(src: str, dst: str, points: list[tuple[int, int]] = (), *, label: str = "", dashed: bool = False,
         exit: tuple[float, float] | None = None, entry: tuple[float, float] | None = None) -> None:
    style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor={MUTED};strokeWidth=1.5;endArrow=block;"
             f"endSize=6;endFill=1;fontSize=11;fontColor={MUTED};labelBackgroundColor=#ffffff;{FONT}"
             f"{'dashed=1;' if dashed else ''}")
    if exit:
        style += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
    if entry:
        style += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    pts = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points)
    arr = f'<Array as="points">{pts}</Array>' if pts else ""
    cells.append(f'<mxCell id="{_id()}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" '
                 f'target="{dst}"><mxGeometry relative="1" as="geometry">{arr}</mxGeometry></mxCell>')


# Columns: client 20..260 | channel | agents and kernel 360..1240 | channel | bank 1340..1620
CX, KX, KW, BX = 20, 360, 880, 1340
IX, IW = KX + 20, KW - 40                       # inner area of the centre column

group("client", "Client device", CX, 20, 240, 640)
group("agents", "Untrusted: agents and models", KX, 20, KW, 180, dashed=True)
kernel = group("kernel", "Trusted bank kernel: decides without any model", KX, 250, KW, 410)
group("bank", "Bank and outside world", BX, 20, 280, 640)
group("audit", "Audit", KX, 710, KW, 140)

chat = box("Chat UI", ("kernel-drawn cards,", "protected sign-in form"), CX + 20, 90, 200, 110)
device = box("Device key", ("Ed25519 or P-256,", "WebCrypto, not extractable"), CX + 20, 376, 200, 80)
box("App sections", ("month, budget, permissions,", "trust, agents, cost,", "red-team, journal"), CX + 20, 500, 200, 120)

bar("Agent passport (know your agent): role, model, instruction hash, one task, session key", IX, 56, IW, 30,
    fill="#fffdf7", stroke="#f3d49b")
aw, ag = 156, 15
agents = [box(t, l, IX + k * (aw + ag), 100, aw, 80) for k, (t, l) in enumerate([
    ("Dispatcher", ("rules and templates", "level 0")), ("Planner", ("plans new tasks", "level 2")),
    ("Readers", ("bills and e-mails", "level 1")), ("Critic", ("sees only the", "client's words, level 1")),
    ("LLM gateway", ("model ladder, budget,", "secret filter, replay"))])]

steps = ["Passport", "Schema", "Mandate", "Provenance", "References", "Policy", "Risk", "Critic", "Shadow run"]
sw, sg = 86, 8
pipe = [box(t, (), IX + k * (sw + sg), 296, sw, 56, size=12) for k, t in enumerate(steps)]
for a, b in zip(pipe, pipe[1:]):
    edge(a, b)
card = bar("Card: what the client sees is exactly what the device signs (hash)", IX, 390, IW,
           fill="#e6e1fd", stroke="#a99cf0", bold=True)
result = bar("Result check: bank records, merchant, inbox", IX, 440, IW)
sw2, sg2 = 197, 17
for k, (t, l) in enumerate([("Mandates", ("VI / AP2 format,", "signed by the device")),
                            ("Secret vault", ("passwords and SMS codes,", "bound to the domain")),
                            ("Trust ledger", ("levels 0 to 2 per task,", "elevated protection")),
                            ("Memory", ("suspicious events,", "templates, envelopes"))]):
    box(t, l, IX + k * (sw2 + sg2), 494, sw2, 76)
bar("Scam-call ladder: explain, pause, 24 h reversible hold, live analyst; duress PIN", IX, 592, IW, 34)

bw = 240
box("Mobile operator", ("ongoing call, spoofed number",), BX + 20, 70, bw, 64)
box("Transfer graph", ("money-mule features and model",), BX + 20, 160, bw, 64)
box("Merchant API", ("sandbox: cancel, cards on file",), BX + 20, 250, bw, 64)
executor = box("Bank executor", ("payments, transfers,", "disputes, merchant blocks"), BX + 20, 380, bw, 96,
               fill="#ffffff", stroke="#7cc5b4")
box("Analyst desk", ("holds and fraud cases",), BX + 20, 520, bw, 64)

aw3, ag3 = 258, 33
journal = box("Signed journal", ("hash chain of every step",), IX, 760, aw3, 64)
pg = box("PostgreSQL", ("append-only, row-level security",), IX + aw3 + ag3, 760, aw3, 64)
anchor = box("AuditAnchor contract", ("Merkle checkpoints on chain",), IX + 2 * (aw3 + ag3), 760, aw3, 64)

# Edges run only through the channels between the columns and rows.
edge(chat, agents[0], label="request", exit=(1, 0.45), entry=(0, 0.5))
edge(agents[1], pipe[0], [(IX + aw + ag + aw // 2, 225), (336, 225), (336, 324)], label="intent + references",
     exit=(0.5, 1), entry=(0, 0.5))
edge(pipe[-1], card, exit=(0.5, 1), entry=(round((8 * (sw + sg) + sw / 2) / IW, 4), 0))
edge(card, chat, [(310, 397), (310, 180)], label="card", exit=(0, 0.2), entry=(1, 0.818))
edge(device, card, label="signature", exit=(1, 0.5), entry=(0, 0.765))
edge(card, executor, label="one-time grant", exit=(1, 0.5), entry=(0, round(27 / 96, 4)))
edge(executor, result, label="verified result", dashed=True, exit=(0, round(77 / 96, 4)), entry=(1, 0.5))
edge(kernel, journal, [(IX + aw3 // 2, 685)], label="every step", exit=(round((IX + aw3 / 2 - KX) / KW, 4), 1), entry=(0.5, 0))
edge(journal, pg)
edge(pg, anchor)

xml = ('<mxfile><diagram name="Architecture" id="a"><mxGraphModel dx="1640" dy="870" grid="0" gridSize="10" '
       'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1640" '
       'pageHeight="870" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
       + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
Path(__file__).with_name("architecture.drawio").write_text(xml, encoding="utf-8")
print(Path(__file__).with_name("architecture.drawio"))
