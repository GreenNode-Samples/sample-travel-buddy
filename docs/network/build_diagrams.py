#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentBase network diagrams in the AWS Architecture Diagram style.

Writes five SVG files next to this script (they render directly in GitHub READMEs):
  01-connectivity-map.svg    overview: Agent Runtime + vCR, LLM / Memory / Access Control, one MCP Gateway + connectors
  02-uc-public.svg           Public use case: Runtime in Public mode, Public gateway, MCP on the Internet and on AgentBase
  03-uc-private-cloud.svg    use case A: Private runtime and gateway, MCP servers in the customer VPC
  04-uc-hybrid-onprem.svg    use case B: Private runtime and gateway, MCP servers in the customer data center
  05-onprem-connectivity.svg on-premises <-> customer VPC connectivity (VPN / Interconnect, routes, firewall, CIDRs)
It also writes docs/architecture.svg of each sibling sample repo (ARCH_JOBS).

Model (GreenNode AgentBase documentation):
  - Agent Runtime and MCP Gateway are managed by GreenNode on the AgentBase Platform, never inside the customer
    VPC. Public mode uses AgentBase's shared public endpoint; Private mode runs in the AgentBase VPC
    (172.30.0.0/16), which is connected privately to the customer VPC. On-premises networks join the customer
    VPC over VPN Site-to-Site or Interconnect.
  - Every MCP tool call goes Agent -> MCP Gateway (Inbound Auth -> Policy Group) -> MCP Connector (URL +
    Outbound Auth, secret from Access Control) -> MCP server. One gateway holds every connector an agent needs
    (Internet, Agent Runtime, customer VPC, on-premises); a Private gateway reaches the VPC and on-premises
    through the private connection.
  - LLM calls are a separate path: directly to the AI Platform or through the Sidecar LLM Proxy (localhost:18080).

Drawing rules: every arrow is a straight line or makes a single right-angle turn (no zig-zags). Nodes on one
request path share a row or a column; the MCP Gateway is drawn as a one-row pipeline (Inbound Auth -> Policy
Group -> connectors) so the caller's tools/call arrow is straight; fan-in / fan-out uses a bus.

Run:  python3 docs/network/build_diagrams.py
"""
import html as H
import pathlib
import re

OUT = pathlib.Path(__file__).resolve().parent

# ── AWS layout; colours and icons follow the GreenNode icon set ──────────────────
INK, SLATE, MUTED = "#232F3E", "#545B64", "#7D8998"
GN_GREEN = "#0DB14B"
C = {
    "compute": "#FF4C00", "ai": "#358DD5", "db": "#00B795", "sec": "#DD344C", "idc": "#00682E",
    "net": "#5B5AA8", "mcp": "#232F3E", "app": "#358DD5", "gray": "#7D8998",
    "teal": "#00A4A6", "dark": "#232F3E",
}
ICONS = OUT / "icons"
# official GreenNode icons (icons/*.svg) and the MCP logo become <symbol>s, reused with <use>
SYMBOLS = {"gn-ai": "ai-platform.svg", "gn-cr": "container-registry.svg", "gn-server": "vserver.svg",
           "gn-vks": "vks.svg", "gn-vdb": "vdb.svg", "gn-vnet": "vnetwork.svg", "mcp": "mcp.svg",
           "agent-runtime": "agent-runtime.svg", "policy": "policy.svg",
           "mcp-gateway": "mcp-gateway.svg", "inbound-auth": "inbound-auth.svg",
           "langfuse": "langfuse.svg", "access-control": "access-control.svg", "memory": "memory.svg"}
FONT = "Helvetica Neue,Helvetica,Arial,sans-serif"


# ── primitives ───────────────────────────────────────────────────────────────
def esc(s):
    return H.escape(s, quote=True)


def text(x, y, s, size=12, weight=400, color=INK, anchor="start", halo=False, italic=False):
    st = []
    if halo:
        st.append("paint-order:stroke;stroke:#FFFFFF;stroke-width:4px;stroke-linejoin:round")
    if italic:
        st.append("font-style:italic")
    style = f' style="{";".join(st)}"' if st else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" '
            f'text-anchor="{anchor}"{style}>{esc(s)}</text>')


def path(d, w=2, color="#FFFFFF", fill="none"):
    return (f'<path d="{d}" fill="{fill}" stroke="{color}" stroke-width="{w}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


# hand-drawn glyphs (48 x 48 frame) for things without an official icon
def g_db(x, y):
    return (path(f"M{x+13} {y+15}a11 4.5 0 0 1 22 0v18a11 4.5 0 0 1 -22 0z")
            + path(f"M{x+13} {y+15}a11 4.5 0 0 0 22 0") + path(f"M{x+13} {y+24}a11 4.5 0 0 0 22 0", 1.6))


def g_firewall(x, y):
    out = path(f"M{x+11} {y+13}h26v22h-26z")
    out += path(f"M{x+11} {y+20.3}h26M{x+11} {y+27.6}h26", 1.6)
    out += path(f"M{x+20} {y+13}v7.3M{x+29} {y+13}v7.3M{x+16} {y+20.3}v7.3M{x+24} {y+20.3}v7.3M{x+32} {y+20.3}v7.3"
                f"M{x+20} {y+27.6}v7.4M{x+29} {y+27.6}v7.4", 1.6)
    return out


def g_server(x, y):
    out = ""
    for i in range(3):
        yy = y + 12 + i * 9
        out += path(f"M{x+12} {yy}h24v7h-24z", 1.8) + f'<circle cx="{x+31}" cy="{yy+3.5}" r="1.6" fill="#FFFFFF"/>'
    return out


def g_app(x, y):
    return (path(f"M{x+10} {y+13}h28v22h-28z") + path(f"M{x+10} {y+19}h28", 1.8)
            + "".join(f'<circle cx="{x+14+i*4}" cy="{y+16}" r="1.2" fill="#FFFFFF"/>' for i in range(3))
            + path(f"M{x+16} {y+25}h10M{x+16} {y+30}h16", 1.6))


def g_globe(x, y):
    return (f'<circle cx="{x+24}" cy="{y+24}" r="12" fill="none" stroke="#FFFFFF" stroke-width="2"/>'
            + path(f"M{x+12} {y+24}h24") + path(f"M{x+24} {y+12}a6 12 0 0 0 0 24a6 12 0 0 0 0 -24", 1.6))


def g_building(x, y):
    return (path(f"M{x+14} {y+36}V{y+13}h13v23M{x+27} {y+20}h8v16M{x+11} {y+36}h27", 2)
            + path(f"M{x+18} {y+18}h5M{x+18} {y+23}h5M{x+18} {y+28}h5", 1.6))


def g_registry(x, y):
    out = ""
    for i in range(3):
        yy = y + 13 + i * 7
        out += path(f"M{x+12} {yy+6}l12-6l12 6l-12 6z", 1.7)
    return out


GLYPH = {"db": g_db, "firewall": g_firewall, "server": g_server, "app": g_app,
         "globe": g_globe, "building": g_building, "registry": g_registry}


def symbol(sid, fname):
    raw = (ICONS / fname).read_text(encoding="utf-8")
    vb = re.search(r'viewBox="([^"]+)"', raw).group(1)
    inner = raw[raw.index(">", raw.index("<svg")) + 1: raw.rindex("</svg>")]
    inner = re.sub(r'id="([^"]+)"', lambda m: f'id="{sid}-{m.group(1)}"', inner).replace("url(#", f"url(#{sid}-")
    return f'<symbol id="{sid}" viewBox="{vb}" fill="none">{inner}</symbol>'


def use(sid, x, y, size):
    return f'<use href="#{sid}" xlink:href="#{sid}" x="{x}" y="{y}" width="{size}" height="{size}"/>'


PAD = 5            # an icon tile extends PAD beyond its 48 x 48 frame on each side (same centre, same layout)
NODE_BOXES: list[tuple[float, float]] = []   # top-left of every icon drawn; arrow() moves end points to the tile edge


def icon(x, y, glyph, color, badge=None):
    """White tile with a grey border (GreenNode style) and the icon; badge = small icon at the top right
    (shows where an MCP server runs)."""
    NODE_BOXES.append((x, y))
    S = 48 + 2 * PAD
    out = (f'<rect x="{x - PAD + 0.75}" y="{y - PAD + 0.75}" width="{S - 1.5}" height="{S - 1.5}" rx="11" '
           f'fill="#FFFFFF" stroke="#CFD6DD" stroke-width="1.5"/>')
    if glyph in SYMBOLS:
        out += use(glyph, x + 2, y + 2, 44)
    else:  # hand-drawn glyph (48 frame), scaled up around its centre
        k = 44 / 32
        out += (f'<g transform="translate({x + 24} {y + 24}) scale({k:.3f}) translate({-x - 24} {-y - 24})">'
                + GLYPH[glyph](x, y).replace("#FFFFFF", color) + '</g>')
    if badge:
        out += (f'<circle cx="{x+49}" cy="{y-1}" r="13" fill="#FFFFFF" stroke="#CFD6DD" stroke-width="1.2"/>'
                + use(badge, x + 39, y - 11, 20))
    return out


def _off_tile(pt, prev):
    """If an end point lies on the 48 frame of an icon, move it out by PAD along the direction of travel."""
    px, py = pt
    for bx, by in NODE_BOXES:
        if px in (bx, bx + 48) and by <= py <= by + 48 and prev[1] == py:
            return (px - PAD if px == bx else px + PAD, py)
        if py in (by, by + 48) and bx <= px <= bx + 48 and prev[0] == px:
            return (px, py - PAD if py == by else py + PAD)
    return pt


def lbl_below(x, y, name, sub=(), color=INK):
    """Label centred under the icon whose top-left corner is (x, y)."""
    cx = x + 24
    out = text(cx, y + 64, name, 12, 700, color, "middle")
    for i, s in enumerate(sub if isinstance(sub, (list, tuple)) else [sub]):
        out += text(cx, y + 79 + i * 14, s, 10.5, 400, SLATE if color == INK else "#D5DBDB", "middle")
    return out


def lbl_right(x, y, name, sub=()):
    out = text(x + 60, y + 20, name, 12, 700)
    for i, s in enumerate(sub if isinstance(sub, (list, tuple)) else [sub]):
        out += text(x + 60, y + 35 + i * 13.5, s, 10.5, 400, SLATE)
    return out


def lbl_topright(x, y, name, sub=()):
    """Label to the right of the icon, above its centre line, so an arrow can leave the icon to the right."""
    out = text(x + 60, y + 2, name, 12, 700)
    for i, s in enumerate(sub if isinstance(sub, (list, tuple)) else [sub]):
        out += text(x + 60, y + 15 + i * 13, s, 10.5, 400, SLATE)
    return out


def node(x, y, glyph, color, name, sub=(), side="below", badge=None):
    lbl = {"below": lbl_below, "right": lbl_right, "topright": lbl_topright}[side]
    return icon(x, y, glyph, color, badge) + lbl(x, y, name, sub)


def people(x, y):
    """AWS 'Users' icon (48 x 48 frame)."""
    out = ""
    for dx, s in [(-11, 0.8), (11, 0.8), (0, 1.0)]:
        cx = x + 24 + dx
        r = 6 * s
        out += (f'<circle cx="{cx}" cy="{y + 16 + (0 if s == 1 else 3)}" r="{r}" fill="#FFFFFF" stroke="{INK}" stroke-width="1.8"/>'
                f'<path d="M{cx - 11 * s} {y + 42}a{11 * s} {12 * s} 0 0 1 {22 * s} 0z" fill="#FFFFFF" stroke="{INK}" stroke-width="1.8"/>')
    return out


def group(x, y, w, h, title, kind):
    """AWS group: frame, icon tab at the top left, title."""
    spec = {
        "cloud": (INK, "", None, INK, "gnmark"),
        "region": (C["teal"], "6 4", None, C["teal"], None),
        "managed": (GN_GREEN, "", None, "#0A8F3C", "gnmark"),
        "vpc": (C["net"], "", None, C["net"], "gn-vnet"),
        "private": (None, "", "#E6F6F7", C["teal"], "lock"),
        "public": (None, "", "#F2F6E8", "#5A7D12", "globe-tab"),
        "onprem": (MUTED, "", None, INK, "building"),
        "internet": (MUTED, "6 4", None, INK, "globe"),
        "shared": (MUTED, "4 3", "#FAFBFC", SLATE, None),
        "sg": (C["sec"], "", None, C["sec"], None),
    }[kind]
    stroke, dash, fill, tcolor, ic = spec
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill or "none"}" '
           f'stroke="{stroke or "none"}" stroke-width="1.5"' + (f' stroke-dasharray="{dash}"' if dash else '') + '/>')
    tx = x + 8
    if ic == "globe-tab":
        out += f'<rect x="{x}" y="{y}" width="26" height="26" fill="#7AA116"/>'
        out += f'<g transform="translate({x} {y}) scale(0.5417)">{GLYPH["globe"](0, 0)}</g>'
        tx = x + 34
    elif ic == "lock":
        out += (f'<rect x="{x}" y="{y}" width="26" height="26" fill="{C["teal"]}"/>'
                f'<rect x="{x+8}" y="{y+12}" width="10" height="8" rx="1.5" fill="#FFFFFF"/>'
                + path(f"M{x+10} {y+12}v-2.5a3 3 0 0 1 6 0v2.5", 1.8))
        tx = x + 34
    elif ic == "gnmark":
        out += (f'<rect x="{x}" y="{y}" width="26" height="26" fill="#FFFFFF" stroke="{stroke}" stroke-width="1.5"/>'
                f'<circle cx="{x+13}" cy="{y+13}" r="7" fill="none" stroke="{GN_GREEN}" stroke-width="3.2"/>'
                f'<circle cx="{x+13}" cy="{y+13}" r="2.6" fill="#333333"/>')
        tx = x + 34
    elif ic in SYMBOLS:
        out += (f'<rect x="{x}" y="{y}" width="26" height="26" fill="#FFFFFF" stroke="{stroke}" stroke-width="1.5"/>'
                + use(ic, x + 4, y + 4, 18))
        tx = x + 34
    elif ic:
        col = MUTED  # remaining tabs: building / globe
        out += f'<rect x="{x}" y="{y}" width="26" height="26" fill="{col}"/>'
        out += f'<g transform="translate({x} {y}) scale(0.5417)">{GLYPH[ic](0, 0)}</g>'
        tx = x + 34
    out += text(tx, y + 18, title, 12.5, 700, tcolor)
    return out


def arrow(pts, kind="req", label=None, at=None, anchor="middle", both=False):
    stroke, width, dash, mk = {
        "req": (INK, 1.6, "", "a-ink"),
        "dx": (C["compute"], 3.2, "", "a-org"),
        "vpn": (C["sec"], 2, "7 4", "a-red"),
    }[kind]
    pts = list(pts)
    if len(pts) >= 2:
        pts[-1] = _off_tile(pts[-1], pts[-2])
        pts[0] = _off_tile(pts[0], pts[1])
    p = " ".join(f"{a},{b}" for a, b in pts)
    out = (f'<polyline points="{p}" fill="none" stroke="{stroke}" stroke-width="{width}" '
           f'stroke-linejoin="round"' + (f' stroke-dasharray="{dash}"' if dash else '')
           + f' marker-end="url(#{mk})"' + (f' marker-start="url(#{mk})"' if both else '') + '/>')
    if label:
        lx, ly = at
        lines = label if isinstance(label, (list, tuple)) else [label]
        for i, s in enumerate(lines):
            out += text(lx, ly + i * 13, s, 10.5, 600, INK if kind == "req" else stroke, anchor, halo=True)
    return out


def line(pts):
    """Connector without an arrowhead (trunk or bus); an end on an icon frame moves out to the tile edge."""
    pts = list(pts)
    pts[0] = _off_tile(pts[0], pts[1])
    pts[-1] = _off_tile(pts[-1], pts[-2])
    return (f'<polyline points="{" ".join(f"{a},{b}" for a, b in pts)}" fill="none" stroke="{INK}" '
            f'stroke-width="1.6" stroke-linejoin="round"/>')


def step(cx, cy, n):
    return (f'<circle cx="{cx}" cy="{cy}" r="10" fill="{INK}" stroke="#FFFFFF" stroke-width="2"/>'
            + text(cx, cy + 4, str(n), 11, 700, "#FFFFFF", "middle"))


def steps_svg(steps, first=1):
    """steps: one entry per step number, each a list of badge positions (parallel paths share a number)."""
    return "".join(step(cx, cy, i) for i, pts in enumerate(steps, first) for cx, cy in pts)


def card(x, y, w, h, title, rows):
    out = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#FFFFFF" stroke="{MUTED}" stroke-width="1"/>'
    out += f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="4" fill="#F2F3F3"/>'
    out += f'<rect x="{x}" y="{y+20}" width="{w}" height="4" fill="#F2F3F3"/>'
    out += text(x + 10, y + 16, title, 11, 700)
    for i, r in enumerate(rows):
        if isinstance(r, tuple):
            a, b = r
            out += (f'<text x="{x+10}" y="{y+42+i*17}" font-size="10.5" fill="{INK}" '
                    f'font-family="Menlo,Consolas,monospace">{esc(a)}</text>')
            out += text(x + w - 10, y + 42 + i * 17, b, 10.5, 600, SLATE, "end")
        else:
            out += text(x + 10, y + 42 + i * 17, r, 10.5, 400, SLATE)
    return out


def legend(x, y, items):
    out, cx = "", x
    for kind, s in items:
        stroke, width, dash = {"req": (INK, 1.6, ""),
                               "dx": (C["compute"], 3.2, ""), "vpn": (C["sec"], 2, "7 4")}[kind]
        out += (f'<line x1="{cx}" y1="{y}" x2="{cx+34}" y2="{y}" stroke="{stroke}" stroke-width="{width}"'
                + (f' stroke-dasharray="{dash}"' if dash else '') + '/>')
        out += text(cx + 42, y + 4, s, 10.5, 400, SLATE)
        cx += 42 + int(len(s) * 5.6) + 28
    return out


def defs(body):
    out = "<defs>"
    for mid, col in [("a-ink", INK), ("a-org", C["compute"]), ("a-red", C["sec"])]:
        out += (f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{col}"/></marker>')
    out += "".join(symbol(k, v) for k, v in SYMBOLS.items() if f'href="#{k}"' in body)   # only symbols in use
    return out + "</defs>"


def svg(w, h, body, label):
    NODE_BOXES.clear()   # the registry is valid for one diagram only
    body = "".join(body)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="{FONT}" role="img" aria-label="{esc(label)}">'
            f'<rect width="{w}" height="{h}" fill="#FFFFFF"/>{defs(body)}{body}</svg>')


# ── AgentBase building blocks ────────────────────────────────────────────────────
SVC_TIP = 120   # an arrow into a shared service stops just under its label (box top + 120; the box is 130 high)


def shared_services(s, x, y, w=490):
    """'Shared services' box: LLM / Memory / Access Control side by side, labels under the icons.
    Returns the x centre of each service, so a caller can draw one arrow per service."""
    s.append(group(x, y, w, 130, "Shared services · platform API", "shared"))
    step_x = (w - 40) // 3
    cx = {}
    for i, (key, sid, name, sub) in enumerate([
            ("llm", "gn-ai", "LLM — AI Platform", "OpenAI-compatible API"),
            ("memory", "memory", "Memory", "short-term + long-term"),
            ("ac", "access-control", "Access Control", "identity · credentials")]):
        nx = x + 40 + i * step_x
        s.append(node(nx, y + 32, sid, INK, name, [sub]))
        cx[key] = nx + 24
    return cx


def to_services(s, trunk, bus_y, xs, tip_y):
    """Agent -> every shared service it uses: a trunk from the agent to a bus, then one arrow per service."""
    s.append(line(trunk))
    lo, hi = min(xs + [trunk[-1][0]]), max(xs + [trunk[-1][0]])
    s.append(f'<polyline points="{lo},{bus_y} {hi},{bus_y}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    for x in xs:
        s.append(arrow([(x, bus_y), (x, tip_y)], "req"))


def connector(x, y, name, url, auth, w=320):
    """One MCP Connector inside the gateway: MCP endpoint URL + Outbound Auth."""
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="56" rx="4" fill="#FFFFFF" '
           f'stroke="#9AA5B1" stroke-width="1.2"/>')
    out += use("mcp", x + 9, y + 10, 36)
    out += (f'<text x="{x+52}" y="{y+18}" font-size="12" font-weight="700" fill="{INK}" '
            f'font-family="Menlo,Consolas,monospace">{esc(name)}</text>')
    out += text(x + 52, y + 33, "URL: " + url, 10.5, 400, SLATE)
    out += text(x + 52, y + 47, "Outbound: " + auth, 10.5, 400, SLATE)
    return out


# connector name -> (endpoint URL, Outbound Auth); "name@variant" shows as "name"
CONNECTORS = {
    "tavily": ("https://<Tavily MCP endpoint>", "API Key · 2LO"),
    "stock": ("MCP runtime endpoint", "API Key · 2LO"),
    "crm": ("https://xx.xx.x.x:8443 · VPC", "No authorization"),
    "inventory": ("https://xx.xx.x.x:8443 · VPC", "API Key · 2LO"),
    "erp": ("https://xx.xx.x.x:8443 · on-prem", "API Key · 2LO"),
    "hr": ("https://xx.xx.x.x:8443 · on-prem", "OAuth · 2LO"),
    "github": ("https://<GitHub MCP endpoint>", "OAuth · 3LO"),
    "erp@onprem": ("https://<onprem-host>:8443/mcp", "API Key · X-Api-Key"),
    "restaurant@vpc": ("https://<mcp-private-ip>:8443/mcp", "API Key · X-Api-Key"),
}


def gateway_pipeline(s, x, row, names, network, title, w=490):
    """MCP Gateway (managed by GreenNode) drawn as a pipeline on one row: Inbound Auth -> Policy Group -> MCP Connectors.
    The caller's tools/call arrow ends at (x + 24, row), so it is a straight line when the caller sits on the same row.
    Connectors fan out symmetrically around the row (connector i of n has its centre at row + (i - (n-1)/2) * 62).
    Returns {connector name: centre y}; every connector's right edge is at x + w - 10."""
    n = len(names)
    mids = {nm: int(row + (i - (n - 1) / 2) * 62) for i, nm in enumerate(names)}
    top = min(mids.values()) - 28 - 38
    h = max(max(mids.values()) + 28, row + 58) + 30 - top
    s.append(f'<rect x="{x}" y="{top}" width="{w}" height="{h}" fill="#F8F7FC" stroke="{C["net"]}" stroke-width="1.5"/>')
    s.append(f'<rect x="{x}" y="{top}" width="26" height="26" fill="#FFFFFF" stroke="{C["net"]}" stroke-width="1.5"/>'
             + use("mcp-gateway", x + 4, top + 4, 18))
    s.append(text(x + 34, top + 18, title, 12.5, 700, C["net"]))
    s.append(text(x + w - 10, top + 18, "MCP Connectors", 11, 700, INK, "end"))
    s.append(node(x + 24, row - 24, "inbound-auth", C["net"], "Inbound Auth", ["IAM · JWT"]))
    s.append(node(x + 124, row - 24, "policy", C["idc"], "Policy Group", ["ALLOW / DENY"]))
    s.append(arrow([(x + 72, row), (x + 124, row)], "req"))
    cx, bus = x + 212, x + 196
    if n == 1:
        s.append(arrow([(x + 172, row), (cx, row)], "req"))
    else:
        s.append(line([(x + 172, row), (bus, row)]))
        lo, hi = min(list(mids.values()) + [row]), max(list(mids.values()) + [row])
        s.append(f'<polyline points="{bus},{lo} {bus},{hi}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
        for m in mids.values():
            s.append(arrow([(bus, m), (cx, m)], "req"))
    for nm, m in mids.items():
        url, auth = CONNECTORS[nm]
        s.append(connector(cx, m - 28, nm.split("@")[0], url, auth, w=w - 222))
    s.append(text(x + 12, top + h - 10, network, 10.5, 600, C["net"]))
    return mids


def abvpc(x, y, w, h):
    """AgentBase VPC (172.30.0.0/16): where Private-mode Agent Runtime and MCP Gateway run, managed by GreenNode."""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{GN_GREEN}" '
            f'stroke-width="1.5" stroke-dasharray="7 4"/>'
            + f'<rect x="{x}" y="{y}" width="26" height="26" fill="#FFFFFF" stroke="{GN_GREEN}" stroke-width="1.5"/>'
            + use("gn-vnet", x + 4, y + 4, 18)
            + text(x + 34, y + 18, "AgentBase VPC · 172.30.0.0/16", 12.5, 700, "#0A8F3C"))


def gateway_compact(s, x, y, title, names, network):
    """Compact MCP Gateway (up to 2 connectors), used where a diagram shows more than one gateway."""
    w, h = 450, 186
    s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#F8F7FC" stroke="{C["net"]}" stroke-width="1.5"/>')
    s.append(f'<rect x="{x}" y="{y}" width="26" height="26" fill="#FFFFFF" stroke="{C["net"]}" stroke-width="1.5"/>'
             + use("mcp-gateway", x + 4, y + 4, 18))
    s.append(text(x + 34, y + 18, title, 12.5, 700, C["net"]))
    s.append(text(x + w - 10, y + 18, "MCP Connectors", 11, 700, INK, "end"))
    for ty, sid, label in [(y + 40, "inbound-auth", "Inbound Auth"), (y + 104, "policy", "Policy Group")]:
        s.append(f'<rect x="{x+12}" y="{ty-2}" width="40" height="40" rx="8" fill="#FFFFFF" stroke="#CFD6DD" stroke-width="1.2"/>'
                 + use(sid, x + 15, ty + 1, 34))
        s.append(text(x + 58, ty + 23, label, 11.5, 700, INK))
    s.append(arrow([(x + 32, y + 78), (x + 32, y + 102)], "req"))
    s.append(text(x + 12, y + h - 8, network, 10.5, 600, C["net"]))
    mids = {}
    for i, n in enumerate(names):
        url, auth = CONNECTORS[n]
        cy = y + 26 + i * 62
        s.append(connector(x + 170, cy, n.split("@")[0], url, auth, w=270))
        mids[n] = cy + 28
    bus = x + 155
    # policy -> bus leaves from the bottom of the Policy tile so it does not cross the "Policy Group" label
    s.append(f'<polyline points="{x+32},{y+142} {x+32},{y+156} {bus},{y+156}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    lo, hi = min(list(mids.values()) + [y + 156]), max(list(mids.values()) + [y + 156])
    s.append(f'<polyline points="{bus},{lo} {bus},{hi}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    for m in mids.values():
        s.append(arrow([(bus, m), (x + 170, m)], "req"))
    return mids


# ═════════════════════════ shared scene for 01 / 03 / 04 ═══════════════════════
# Cross layout around the Agent Runtime, so every arrow is straight or turns once:
#   vCR -> Runtime from the left · Runtime -> shared services above (trunk + bus) · internal app -> Runtime from below
#   · Runtime -> MCP Gateway on the same row · each connector leaves to the right and turns once: up to the Internet
#   or to an MCP server on Agent Runtime, down into the customer VPC or to the VPN GW. A connector higher in the
#   gateway that turns down uses a column further right, so no two arrows cross.
def scene(show, conns, title):
    has = lambda k: k in show
    W = 1760 if has("onprem") else 1440
    top = 0 if has("internet") else 110
    ROW, GX, L = 610, 600, 830               # Runtime / gateway row · gateway left edge · top of the customer VPC
    RX = 420                                 # Agent Runtime and internal app column (icon left edge)
    XA, XS, XC, XI, XE = 1120, 1220, 1150, 1270, 1300   # columns: Internet · hosted MCP · crm · inventory · VPN GW
    Hh = L + 520 - top
    s = []
    if has("internet"):
        s.append(group(20, 16, W - 40, 94, "Internet", "internet"))
        s.append(node(130, 40, "registry", C["gray"], "Public registry", ["alternative to vCR (opt-in)", "Docker Hub · GHCR …"], "right"))
        s.append(node(XA - 24, 40, "mcp", C["mcp"], "MCP servers on the Internet", ["Tavily · GitHub · Slack …"], "right"))

    s.append(group(20, 130, 1390, L + 340, "GreenNode Cloud", "cloud"))
    s.append(group(34, 166, 1362, L + 290, "Region HCM", "region"))
    s.append(group(50, 200, 1330, 590, "AgentBase Platform — managed by GreenNode", "managed"))
    svc = shared_services(s, 180, 236, 460)
    s.append(abvpc(190, 420, 1170, 350))
    s.append(node(90, ROW - 24, "gn-cr", C["compute"], "Container Registry", ["vCR"]))
    s.append(node(RX, ROW - 24, "agent-runtime", C["compute"], "Agent Runtime", ["sidecar LLM :18080"], "topright"))
    net = "Network: Private · Route CIDRs = on-prem CIDR" if has("onprem") else "Network: Private → customer VPC"
    mids = gateway_pipeline(s, GX, ROW, conns, net, "MCP Gateway · Private")
    if has("hosted"):
        s.append(node(XS - 24, 300, "mcp", C["mcp"], "MCP server", ["on Agent Runtime"], "right"))

    s.append(group(50, L, 1330, 440, "Customer VPC · xx.xx.x.x/xx", "vpc"))
    s.append(group(70, L + 40, 450, 150, "Private subnet · app", "private"))
    s.append(node(RX, L + 80, "app", C["app"], "Internal app", ["customer internal system"]))
    rows = [("xx.xx.x.x/xx", "local (VPC)"), ("172.30.0.0/16", "→ AgentBase (private)")]
    if has("onprem"):
        rows += [("xx.xx.x.x/xx", "on-prem → VPN GW / Interconnect")]
    s.append(card(70, L + 220, 450, 30 + 17 * len(rows) + 10, "Route table (customer VPC)", rows))
    if has("vpcmcp"):
        s.append(group(900, L + 40, 420 if "inventory" in conns else 360, 200, "Private subnet · MCP", "private"))
        s.append(node(940, L + 100, "gn-vdb", C["db"], "vDB", ["database"]))
        s.append(node(XC - 24, L + 100, "mcp", C["mcp"], "mcp-crm", ["vServer"], badge="gn-server"))
        if "inventory" in conns:
            s.append(node(XI - 24, L + 100, "mcp", C["mcp"], "mcp-inventory", ["VKS"], badge="gn-vks"))
    if has("onprem"):
        s.append(node(XE - 24, L + 300, "gn-vnet", C["net"], "VPN GW", ["/ Interconnect"]))
        s.append(group(1430, L, 310, 440, "On-premises · xx.xx.x.x/xx", "onprem"))
        s.append(node(1480, L + 300, "firewall", C["gray"], "Firewall", ["allow + return route", "VPC CIDR · 172.30.0.0/16"]))
        s.append(node(1640, L + 300, "mcp", C["mcp"], "mcp-erp" if "hr" not in conns else "mcp-hr", ["xx.xx.x.x"]))
        if "hr" in conns:
            s.append(node(1640, L + 150, "mcp", C["mcp"], "mcp-erp", ["xx.xx.x.x"]))

    # ── flows: straight, or one right-angle turn ──
    steps = []
    s.append(arrow([(138, ROW), (RX, ROW)], "req", ["pull image", "(at deploy)"], ((138 + RX) // 2, ROW - 22)))
    # 1 · the internal app invokes the agent over the private connection, straight up
    s.append(arrow([(RX + 24, L + 80), (RX + 24, ROW + 24)], "req", ["invoke", "(private)"], (RX + 34, 700), "start"))
    steps.append([(RX + 24, 810)])
    # 2 · Agent -> LLM, Memory and Access Control: trunk up, one arrow per service
    to_services(s, [(RX + 24, ROW - 24), (RX + 24, 400)], 400, [svc["llm"], svc["memory"], svc["ac"]], 236 + SVC_TIP)
    s.append(text(RX + 14, 480, "LLM (direct or sidecar :18080)", 10.5, 600, INK, "end", halo=True)
             + text(RX + 14, 493, "Memory · Access Control", 10.5, 600, INK, "end", halo=True))
    steps.append([(RX + 24, 510)])
    # 3 · MCP tools/call, on the Runtime's row
    s.append(arrow([(RX + 48, ROW), (GX + 24, ROW)], "req", "MCP tools/call", ((RX + 48 + GX) // 2, ROW + 18)))
    steps.append([(GX - 12, ROW)])
    out = GX + 480                           # right edge of every connector
    if has("internet"):
        t = mids["tavily"]
        s.append(arrow([(out, t), (XA, t), (XA, 88)], "req"))
        steps.append([(XA, 300)])
    if has("hosted"):
        st = mids["stock"]
        s.append(arrow([(out, st), (XS, st), (XS, 348)], "req"))
        steps.append([(XS, 470)])
    if has("vpcmcp"):
        c = mids["crm"]
        s.append(arrow([(out, c), (XC, c), (XC, L + 100)], "req"))
        s.append(arrow([(XC - 24, L + 124), (988, L + 124)], "req"))
        s.append(text(XC + 10, L + 66, "private IP", 10.5, 600, INK, "start", halo=True))
        badges = [(XC, 810)]
        if "inventory" in conns:
            iv = mids["inventory"]
            s.append(arrow([(out, iv), (XI, iv), (XI, L + 100)], "req"))
            badges.append((XI, 810))
        steps.append(badges)
    if has("onprem"):
        if "hr" in conns:                    # two parallel arrows into the VPN GW: the higher connector lands further right
            e, h_ = mids["erp"], mids["hr"]
            s.append(arrow([(out, e), (XE + 12, e), (XE + 12, L + 300)], "req"))
            s.append(arrow([(out, h_), (XE - 12, h_), (XE - 12, L + 300)], "req"))
            steps.append([(XE + 12, 770), (XE - 12, 810)])
        else:
            e = mids["erp"]
            s.append(arrow([(out, e), (XE, e), (XE, L + 300)], "req"))
            steps.append([(XE, 810)])
        s.append(arrow([(XE + 24, L + 314), (1480, L + 314)], "dx", both=True))
        s.append(arrow([(XE + 24, L + 334), (1480, L + 334)], "vpn", both=True))
        s.append(arrow([(1528, L + 324), (1640, L + 324)], "req"))
        if "hr" in conns:
            s.append(line([(1584, L + 324), (1584, L + 174)]))
            s.append(arrow([(1584, L + 174), (1640, L + 174)], "req"))
    s.append(steps_svg(steps))

    lg = [("req", "request / data path")]
    if has("onprem"):
        lg += [("dx", "Interconnect / leased line"), ("vpn", "Site-to-Site VPN (IPsec)")]
    body = f'<g transform="translate(0 {-top})">{"".join(s)}</g>' + legend(30, Hh - 16, lg)
    return svg(W, Hh, [body], title)


def d1():
    return scene({"internet", "hosted", "vpcmcp", "onprem"}, ["tavily", "stock", "erp", "crm"],
                 "AgentBase connectivity map: Agent Runtime in the AgentBase VPC calls one Private MCP Gateway whose connectors "
                 "reach MCP servers on the Internet, on Agent Runtime, in the customer VPC and on-premises via VPN / Interconnect")


def d2():
    return scene({"vpcmcp"}, ["inventory", "crm"],
                 "Use case A: a Private MCP Gateway calls MCP servers in the customer VPC through connectors")


def d3():
    return scene({"onprem"}, ["erp", "hr"],
                 "Use case B: a Private MCP Gateway calls on-premises MCP servers via VPN or Interconnect")


# ═════════════════════════ Public use case: Agent Runtime in PUBLIC mode ═══════════
# Internet users call the Runtime's public endpoint; the gateway is Public; MCP on the Internet and on AgentBase.
def d_public():
    W, Hh, ROW = 1500, 880, 560
    RX, GX = 390, 680                        # Agent Runtime icon left edge · gateway left edge
    s = []
    # one Internet band on top: users / apps straight above the Runtime, MCP servers on the Internet on the right
    s.append(group(20, 16, W - 40, 94, "Internet", "internet"))
    s.append(people(RX, 36) + lbl_right(RX, 36, "Users / apps", ["browser · webhook · A2A client"]))
    s.append(node(1188, 40, "mcp", C["mcp"], "MCP servers on the Internet", ["Tavily · GitHub · Slack …"], "right"))
    s.append(group(20, 130, 1360, 710, "GreenNode Cloud", "cloud"))
    s.append(group(34, 166, 1332, 660, "Region HCM", "region"))
    s.append(group(50, 200, 1300, 610, "AgentBase Platform — managed by GreenNode", "managed"))
    svc = shared_services(s, 480, 236, 460)
    s.append(node(90, ROW - 24, "gn-cr", C["compute"], "Container Registry", ["vCR"]))
    s.append(node(RX, ROW - 24, "agent-runtime", C["compute"], "Agent Runtime", ["PUBLIC mode · sidecar LLM :18080"], "topright"))
    mids = gateway_pipeline(s, GX, ROW, ["tavily", "github", "stock"], "Network: Public", "MCP Gateway · Public")
    s.append(node(1260, mids["stock"] - 24, "mcp", C["mcp"], "MCP server", ["on Agent Runtime"]))
    s.append(card(70, 650, 430, 130, "PUBLIC mode", [
        ("Runtime endpoint", "public HTTPS (IAM / JWT)"),
        ("Gateway network", "Public"),
        ("VPC / on-prem", "not required"),
        ("Best for", "demos, MCP SaaS, public tools"),
    ]))

    steps = []
    # 1 · users -> the Runtime's public endpoint, straight down
    s.append(arrow([(RX + 12, 88), (RX + 12, ROW - 24)], "req", ["HTTPS", "public endpoint"], (RX + 2, 300), "end"))
    steps.append([(RX + 12, 360)])
    # the image is pulled at deploy time: no step number
    s.append(arrow([(138, ROW), (RX, ROW)], "req", ["pull image", "(at deploy)"], (264, ROW - 22)))
    # 2 · Agent -> LLM, Memory and Access Control, one arrow per service
    to_services(s, [(RX + 36, ROW - 24), (RX + 36, 400)], 400, [svc["llm"], svc["memory"], svc["ac"]], 236 + SVC_TIP)
    s.append(text(RX + 46, 456, "LLM (direct or sidecar :18080)", 10.5, 600, INK, "start", halo=True)
             + text(RX + 46, 469, "Memory · Access Control", 10.5, 600, INK, "start", halo=True))
    steps.append([(RX + 36, 500)])
    # 3 · MCP tools/call, on the Runtime's row
    s.append(arrow([(RX + 48, ROW), (GX + 24, ROW)], "req", "MCP tools/call", ((RX + 48 + GX) // 2, ROW + 18)))
    steps.append([(GX - 12, ROW)])
    out = GX + 480
    # 4, 5 · Internet connectors turn up once; the lower one uses the column further right
    s.append(arrow([(out, mids["tavily"]), (1200, mids["tavily"]), (1200, 88)], "req"))
    steps.append([(1200, 300)])
    s.append(arrow([(out, mids["github"]), (1224, mids["github"]), (1224, 88)], "req"))
    steps.append([(1224, 360)])
    # 6 · MCP server on Agent Runtime, straight
    s.append(arrow([(out, mids["stock"]), (1260, mids["stock"])], "req"))
    steps.append([(1220, mids["stock"])])
    s.append(steps_svg(steps))
    s.append(legend(30, Hh - 16, [("req", "request / data path")]))
    return svg(W, Hh, s, "Public use case: Internet users call the public Agent Runtime endpoint; a Public MCP Gateway "
               "calls MCP servers on the Internet and on Agent Runtime through connectors")


# ═════════════════════════ 05 · on-premises <-> customer VPC ══════════════════════
def d4():
    W, Hh, T = 1360, 1000, 180               # T: the connectivity layout sits under the AgentBase block
    s = []
    # AgentBase is managed by GreenNode outside the customer VPC and connected privately into it
    s.append(group(20, 20, 470, 140, "AgentBase Platform — managed by GreenNode", "managed"))
    s.append(abvpc(36, 56, 438, 92))
    s.append(node(60, 92, "agent-runtime", C["compute"], "Agent Runtime", ["Private mode"], "right"))
    s.append(node(260, 92, "mcp-gateway", C["net"], "MCP Gateway", ["Private mode"], "right"))

    b = []
    # customer VPC
    b.append(group(20, 20, 470, 600, "Customer VPC on GreenNode · xx.xx.x.x/xx", "vpc"))
    b.append(group(36, 60, 300, 120, "subnet-mcp · xx.xx.x.x/xx", "private"))
    b.append(node(60, 96, "mcp", C["mcp"], "MCP servers (cloud)", ["vServer / VKS · xx.xx.x.x"], "right", badge="gn-server"))
    b.append(node(380, 236, "gn-vnet", C["net"], "VPN GW", ["/ Interconnect"]))
    b.append(card(36, 360, 438, 240, "VPC route table", [
        ("xx.xx.x.x/xx", "local (VPC)"),
        ("172.30.0.0/16", "→ AgentBase (private)"),
        ("xx.xx.x.x/xx", "on-prem → VPN GW / Interconnect"),
        "AgentBase (Private mode):",
        ("Route CIDRs", "[\"xx.xx.x.x/xx\"] (on-prem)"),
        ("connector URL", "https://xx.xx.x.x:8443"),
        ("vDNS", "enabled (required)"),
    ]))
    # private connection from the AgentBase VPC into the customer VPC, on to the VPN GW / Interconnect
    b.append(arrow([(404, -32), (404, 236)], "req"))
    b.append(text(414, 6, "private connection", 10.5, 600, INK, "start", halo=True))

    # middle: the two options, card A above the links and card B below them
    b.append(text(680, 40, "Choose one (or both for redundancy)", 12, 700, INK, "middle"))
    b.append(f'<rect x="530" y="60" width="300" height="160" rx="6" fill="#FDF2F3" stroke="{C["sec"]}" stroke-width="1"/>')
    b.append(text(546, 84, "A · Site-to-Site VPN (IPsec)", 12, 700, C["sec"]))
    for r, t in enumerate(["over the Internet, IPsec-encrypted", "IKEv2 · 1 tunnel per on-prem CIDR",
                           "static routes in the VPC route table", "quick to set up, low cost",
                           "bandwidth / latency depend on the Internet"]):
        b.append(text(546, 110 + r * 18, "· " + t, 10.5, 400, SLATE))
    b.append(f'<rect x="530" y="310" width="300" height="190" rx="6" fill="#FEF5EC" stroke="{C["compute"]}" stroke-width="1"/>')
    b.append(text(546, 334, "B · Interconnect / leased line", 12, 700, C["compute"]))
    for r, t in enumerate(["dedicated link DC ↔ GreenNode", "does not traverse the Internet", "fixed bandwidth + SLA",
                           "banking, sensitive data", "pair with VPN as backup"]):
        b.append(text(546, 360 + r * 18, "· " + t, 10.5, 400, SLATE))

    # data center
    b.append(group(870, 20, 470, 600, "Customer data center · xx.xx.x.x/xx", "onprem"))
    b.append(node(900, 236, "firewall", C["gray"], "Customer GW", ["firewall / router"]))
    b.append(group(1010, 60, 314, 270, "Internal DMZ · xx.xx.x.x/xx", "sg"))
    b.append(node(1034, 96, "mcp", C["mcp"], "mcp-erp", ["xx.xx.x.x:8443"], "right"))
    b.append(node(1034, 176, "mcp", C["mcp"], "mcp-hr", ["xx.xx.x.x:8443"], "right"))
    b.append(node(1034, 256, "server", C["gray"], "Internal DNS", ["mcp.corp.local (optional)"], "right"))
    b.append(card(886, 360, 438, 240, "DC route table + firewall", [
        ("xx.xx.x.x/xx", "local (on-prem)"),
        ("xx.xx.x.x/xx", "VPC → tunnel / Interconnect"),
        ("172.30.0.0/16", "→ tunnel / Interconnect"),
        "Firewall rules (inbound to MCP):",
        ("src VPC CIDR, 172.30.0.0/16", "allow tcp/8443"),
        ("udp 500/4500, ESP", "from GreenNode VPN IP"),
        ("everything else", "deny"),
        ("TLS", "internal or public CA certificate"),
    ]))

    # links: two straight, parallel lines between the VPN GW / Interconnect and the customer GW
    b.append(arrow([(428, 250), (900, 250)], "vpn", both=True))
    b.append(text(680, 242, "A · IPsec over the Internet", 10.5, 600, C["sec"], "middle", halo=True))
    b.append(arrow([(428, 270), (900, 270)], "dx", both=True))
    b.append(text(680, 290, "B · Interconnect", 10.5, 600, C["compute"], "middle", halo=True))

    # CIDR plan
    b.append(text(20, 670, "CIDR plan — these three ranges must not overlap", 12.5, 700, INK))
    bars = [(20, 440, C["net"], "xx.xx.x.x/xx", "Customer VPC on GreenNode"),
            (470, 440, C["gray"], "xx.xx.x.x/xx", "Data center on-premises"),
            (920, 420, C["sec"], "172.30.0.0/16", "AgentBase VPC (managed by GreenNode)")]
    for x, w, col, a, lbl in bars:
        b.append(f'<rect x="{x}" y="686" width="{w}" height="40" rx="4" fill="{col}" fill-opacity="0.12" stroke="{col}"/>')
        b.append(f'<text x="{x+12}" y="711" font-size="12" font-weight="700" fill="{col}" '
                 f'font-family="Menlo,Consolas,monospace">{a}</text>')
        b.append(text(x + w - 12, 711, lbl, 11, 400, SLATE, "end"))
    b.append(legend(20, 776, [("req", "private connection"), ("vpn", "Site-to-Site VPN (IPsec, over the Internet)"),
                              ("dx", "Interconnect / leased line (dedicated)")]))
    s.append(f'<g transform="translate(0 {T})">{"".join(b)}</g>')
    return svg(W, Hh, s, "On-premises to customer VPC connectivity on GreenNode: the AgentBase VPC connects privately to the "
               "customer VPC, which reaches the data center over Site-to-Site VPN or Interconnect, with routes and firewall on both ends")


# ═════════════════════════ architecture of each sample repo ═══════════════════════
# Each diagram is written to docs/<name>.svg of the matching sibling repo (see ARCH_JOBS).
def a_travel():
    """sample-travel-buddy: web users -> Runtime (UI + LangGraph) -> LLM / Memory; Tavily through a Public MCP Gateway.
    One Internet band on top holds both the web users and Tavily; the tools/call path is one straight row."""
    W, Hh, ROW = 1500, 530, 354
    s = []
    s.append(group(20, 16, W - 40, 100, "Internet", "internet"))
    s.append(people(120, 44) + lbl_right(120, 44, "Web users", ["Chat UI · REST · A2A"]))
    s.append(node(1300, 44, "mcp", C["mcp"], "Tavily MCP", ["web search · extract"], "right"))
    s.append(group(290, 150, 830, 330, "AgentBase Platform — managed by GreenNode", "managed"))
    s.append(node(340, 190, "gn-ai", C["ai"], "LLM — AI Platform", ["direct or sidecar :18080"]))
    s.append(node(500, 190, "memory", C["db"], "Memory", ["CUSTOM + SEMANTIC"]))
    s.append(node(360, ROW - 24, "agent-runtime", C["compute"], "travel-buddy", ["Agent Runtime · Public", "UI + LangGraph"]))
    mids = gateway_pipeline(s, 600, ROW, ["tavily"], "Network: Public", "MCP Gateway · Public")
    t = mids["tavily"]
    cx = 600 + 212 + (490 - 222) // 2        # centre of the tavily connector
    s.append(node(cx - 24, 180, "access-control", C["idc"], "Access Control", ["secret: tavily-apikey"]))

    s.append(arrow([(144, 94), (144, ROW), (360, ROW)], "req", "HTTPS", (250, ROW - 8)))
    to_services(s, [(384, ROW - 24), (384, 300)], 300, [364, 524], 276)
    s.append(arrow([(408, ROW), (624, ROW)], "req", "tools/call", (516, ROW - 8)))
    s.append(arrow([(cx, 268), (cx, t - 28)], "req", "API key", (cx - 14, 304), "end"))
    s.append(arrow([(1080, t), (1324, t), (1324, 92)], "req", "HTTPS · API key", (1200, t - 8)))
    s.append(steps_svg([[(144, 230)], [(444, 300)], [(560, ROW)], [(cx, 274)], [(1324, 230)]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path")]))
    return svg(W, Hh, s, "travel-buddy architecture: web users call the Agent Runtime; the agent uses the LLM and Memory, "
               "and Tavily through a Public MCP Gateway whose connector takes its API key from Access Control")


def a_zalo():
    """sample-zalo-restaurant: Zalo -> public webhook proxy -> Private Agent Runtime -> Private MCP Gateway ->
    MCP server in the customer VPC; replies through the Zalo Bot API; traces go to a private self-hosted
    Langfuse that admins open over a client-to-site VPN.
    Grid: the Runtime row holds Zalo (reply), the Runtime and the gateway; the webhook proxy sits straight under
    the Runtime; every arrow is straight or turns once."""
    W, Hh = 1470, 1020
    R, T, P, A = 380, 640, 760, 860          # rows: Runtime / gateway · Langfuse · webhook proxy · admin VPN
    RX, GX, X7 = 580, 800, 1320              # Runtime icon left edge · gateway left edge · MCP server column
    s = []
    s.append(group(20, 60, 230, 920, "Internet", "internet"))
    s.append(people(40, 100) + lbl_right(40, 100, "Customers", ["chat on Zalo"]))
    s.append(node(40, R - 24, "globe", C["gray"], "Zalo Bot Platform", ["webhook · Bot API"], "topright"))
    s.append(node(40, A - 24, "app", C["app"], "Admin", ["VPN client"], "topright"))

    s.append(group(270, 20, 1180, 960, "GreenNode Cloud", "cloud"))
    s.append(group(300, 60, 1120, 450, "AgentBase Platform — managed by GreenNode", "managed"))
    # LLM straight above the Runtime, Access Control straight above the connector
    cx = GX + 212 + (490 - 222) // 2
    svc = shared_services(s, RX + 24 - 64, 96, 3 * ((cx - RX - 24) // 2) + 40)
    s.append(abvpc(310, 266, 1100, 230))
    s.append(node(RX, R - 24, "agent-runtime", C["compute"], "Agent Runtime", ["Private · IP allow: proxy"], "topright"))
    mids = gateway_pipeline(s, GX, R, ["restaurant@vpc"], "Network: Private → customer VPC", "MCP Gateway · Private")
    r = mids["restaurant@vpc"]

    s.append(group(300, 540, 1130, 420, "Customer VPC · 10.20.0.0/16", "vpc"))
    s.append(group(320, 580, 330, 360, "Public subnet", "public"))
    s.append(node(RX, P - 24, "gn-server", C["net"], "Webhook proxy", ["vServer · Caddy", "POST /webhook/zalo only"]))
    s.append(node(400, A - 24, "firewall", C["gray"], "Admin VPN", ["pfSense / OpenVPN"]))
    s.append(group(670, 580, 430, 360, "Private subnet · observability", "private"))
    s.append(node(720, T - 24, "langfuse", C["ai"], "Langfuse", ["vServer / VKS · :3000"], "topright"))
    s.append(node(940, T - 24, "db", C["gray"], "Langfuse storage", ["Postgres · ClickHouse · Redis · MinIO"]))
    s.append(card(770, 740, 310, 112, "Inbound rules (SG / Network ACL)", [
        ("proxy :443", "Internet (Zalo webhook)"),
        ("mcp :8443", "AgentBase source range"),
        ("langfuse :3000", "AgentBase range + VPN pool"),
        ("vpn :1194/udp", "admin IPs only"),
    ]))
    # wide enough that its title ends left of the connector arrow coming down at X7
    s.append(group(1120, 580, 290, 360, "Private subnet · MCP", "private"))
    s.append(node(X7 - 24, T, "mcp", C["mcp"], "zalo-mcp-server", ["vServer / VKS"], badge="gn-server"))
    s.append(node(X7 - 24, P + 40, "db", C["gray"], "SQLite volume", ["menu · bookings"]))

    # 1 · the guest writes on Zalo
    s.append(arrow([(64, 150), (64, R - 24)], "req", "message", (74, 250), "start"))
    # 2 · Zalo calls the webhook on the public proxy (only POST /webhook/zalo is forwarded)
    s.append(arrow([(64, R + 24), (64, P), (RX, P)], "req", "HTTPS webhook", (450, P - 8)))
    # 3 · proxy -> Private runtime endpoint, straight up (the runtime's IP allow-list admits only the proxy)
    s.append(arrow([(RX + 12, P - 24), (RX + 12, R + 24)], "req", "private", (RX + 2, 560), "end"))
    # 4 · Agent -> LLM (straight up) and Memory
    to_services(s, [(RX + 24, R - 24), (RX + 24, 250)], 250, [svc["llm"], svc["memory"]], 96 + SVC_TIP)
    s.append(text(RX + 34, 300, "LLM · Memory", 10.5, 600, INK, "start", halo=True))
    # 5 · MCP tools/call, on the Runtime's row
    s.append(arrow([(RX + 48, R), (GX + 24, R)], "req", "MCP tools/call", ((RX + 48 + GX) // 2, R + 18)))
    # 6 · Access Control -> connector, straight down: the API key, never seen by the agent
    s.append(arrow([(cx, 214), (cx, r - 28)], "req", "API key", (cx - 10, 300), "end"))
    # 7 · connector -> MCP server in the customer VPC
    s.append(arrow([(GX + 480, r), (X7, r), (X7, T)], "req", "HTTPS :8443 · X-Api-Key", (X7 - 10, 528), "end"))
    # 8 · reply: the agent calls the Zalo Bot API (sendMessage), straight left
    s.append(arrow([(RX, R), (88, R)], "req", "sendMessage (reply)", (420, R - 8)))
    # 9 · traces to the private Langfuse
    s.append(arrow([(RX + 36, R + 24), (RX + 36, T), (720, T)], "req", "traces (OTel)", (RX + 46, 528), "start"))
    s.append(arrow([(768, T), (940, T)], "req"))
    # 10 · admins: client-to-site VPN, then the Langfuse UI on a private IP
    s.append(arrow([(88, A), (400, A)], "vpn", both=True))
    s.append(arrow([(448, A), (744, A), (744, T + 24)], "req", "Langfuse UI", (596, A - 8)))
    s.append(arrow([(X7, T + 80), (X7, P + 40)], "req"))
    s.append(steps_svg([[(64, 300)], [(300, P)], [(RX + 12, 680)], [(RX + 24, 270)], [(GX - 12, R)], [(cx, 250)],
                        [(X7, 450)], [(300, R)], [(RX + 36, 600)], [(250, A)]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path"), ("vpn", "client-to-site VPN (admins)")]))
    return svg(W, Hh, s, "Zalo restaurant architecture: Zalo webhooks reach a Private Agent Runtime through a public proxy; "
               "the agent calls the MCP server in the customer VPC via a Private MCP Gateway, replies through the Zalo Bot "
               "API and sends traces to a private self-hosted Langfuse that admins open over a client VPN")


def a_stock():
    """sample-mcp-stock-server: the same image deployed in three places, side by side:
    (a) Agent Runtime behind a Public gateway, (b) vServer / VKS in the customer VPC behind a Private gateway,
    (c) on-premises behind a Private gateway with Route CIDRs. The server always calls the public 24hMoney API."""
    W, Hh = 1620, 836
    s = []
    cols = [(20, "(a) Agent Runtime on AgentBase", "MCP Gateway: Public"),
            (560, "(b) vServer / VKS in your VPC", "MCP Gateway: Private (VPC + Subnet)"),
            (1100, "(c) On-premises data center", "MCP Gateway: Private + Route CIDRs (on-prem)")]
    for x0, head, sub in cols:
        s.append(text(x0, 34, head, 14, 700, INK) + text(x0, 52, sub, 11.5, 400, SLATE))
        s.append(group(x0, 66, 500, 214, "AgentBase Platform — managed by GreenNode", "managed"))
        s.append(group(x0, 670, 500, 126, "Internet", "internet"))

    # (a) everything on AgentBase; the runtime endpoint is public, so both protection layers apply
    x0 = 20
    s.append(node(x0 + 40, 130, "agent-runtime", C["compute"], "Agent", ["Agent Runtime"]))
    s.append(node(x0 + 200, 130, "mcp-gateway", C["net"], "MCP Gateway", ["Public · connector stock"]))
    s.append(node(x0 + 360, 130, "mcp", C["mcp"], "vn-stock-mcp", ["Agent Runtime · Public"], badge="agent-runtime"))
    s.append(card(x0, 310, 330, 95, "Public runtime endpoint", [
        ("API key", "fail-closed: 401 / 503"),
        ("IP Access Control", "allowed source CIDRs"),
        ("VPC / VPN", "not needed"),
    ]))
    s.append(node(x0 + 360, 700, "globe", C["gray"], "24hMoney API", ["public · unofficial"]))
    s.append(arrow([(x0 + 88, 154), (x0 + 200, 154)], "req", "tools/call", (x0 + 144, 146)))
    s.append(arrow([(x0 + 248, 154), (x0 + 360, 154)], "req", "X-Api-Key", (x0 + 304, 146)))
    s.append(arrow([(x0 + 384, 216), (x0 + 384, 700)], "req", "HTTPS", (x0 + 394, 600), "start"))

    # (b) Private gateway -> private connection -> server in a private subnet; egress through NAT / proxy
    x0 = 560
    s.append(abvpc(x0 + 12, 100, 476, 170))
    s.append(node(x0 + 60, 150, "agent-runtime", C["compute"], "Agent", ["Agent Runtime · Private"]))
    s.append(node(x0 + 250, 150, "mcp-gateway", C["net"], "MCP Gateway", ["Private · connector stock"]))
    s.append(group(x0, 300, 500, 300, "Customer VPC · xx.xx.x.x/xx", "vpc"))
    s.append(group(x0 + 16, 340, 330, 240, "Private subnet · MCP", "private"))
    s.append(node(x0 + 250, 420, "mcp", C["mcp"], "vn-stock-mcp", ["vServer / VKS · :8443", "from 172.30.0.0/16"], badge="gn-server"))
    s.append(node(x0 + 420, 420, "gn-vnet", C["net"], "NAT / proxy", ["Internet egress"]))
    s.append(node(x0 + 420, 700, "globe", C["gray"], "24hMoney API", ["public · unofficial"]))
    s.append(arrow([(x0 + 108, 174), (x0 + 250, 174)], "req", "tools/call", (x0 + 179, 166)))
    s.append(arrow([(x0 + 274, 246), (x0 + 274, 420)], "req"))
    s.append(text(x0 + 284, 292, "private connection", 10.5, 600, INK, "start", halo=True))
    s.append(arrow([(x0 + 298, 444), (x0 + 420, 444)], "req", "24hMoney :443", (x0 + 359, 436)))
    s.append(arrow([(x0 + 444, 506), (x0 + 444, 700)], "req", "HTTPS", (x0 + 454, 640), "start"))

    # (c) Private gateway (Route CIDRs = on-prem) -> customer VPC -> VPN / Interconnect -> data center
    x0 = 1100
    s.append(abvpc(x0 + 12, 100, 476, 170))
    s.append(node(x0 + 60, 150, "agent-runtime", C["compute"], "Agent", ["Agent Runtime · Private"]))
    s.append(node(x0 + 250, 150, "mcp-gateway", C["net"], "MCP Gateway", ["Private · Route CIDRs = on-prem"]))
    s.append(group(x0, 300, 500, 150, "Customer VPC · xx.xx.x.x/xx", "vpc"))
    s.append(node(x0 + 250, 330, "gn-vnet", C["net"], "VPN GW / Interconnect", ["route: on-prem CIDR"]))
    s.append(group(x0, 480, 500, 160, "On-premises · xx.xx.x.x/xx", "onprem"))
    s.append(node(x0 + 250, 520, "firewall", C["gray"], "Firewall", ["allow VPC CIDR", "and 172.30.0.0/16"]))
    s.append(node(x0 + 90, 520, "mcp", C["mcp"], "vn-stock-mcp", ["host · :8443"]))
    s.append(node(x0 + 90, 700, "globe", C["gray"], "24hMoney API", ["public · unofficial"]))
    s.append(arrow([(x0 + 108, 174), (x0 + 250, 174)], "req", "tools/call", (x0 + 179, 166)))
    s.append(arrow([(x0 + 274, 238), (x0 + 274, 330)], "req"))
    s.append(text(x0 + 284, 292, "private connection", 10.5, 600, INK, "start", halo=True))
    s.append(arrow([(x0 + 274, 418), (x0 + 274, 520)], "vpn", both=True))
    s.append(text(x0 + 284, 466, "VPN (IPsec) / Interconnect", 10.5, 600, C["sec"], "start", halo=True))
    s.append(arrow([(x0 + 250, 544), (x0 + 138, 544)], "req", "tcp/8443", (x0 + 194, 536)))
    s.append(arrow([(x0 + 114, 606), (x0 + 114, 700)], "req", "HTTPS via DC proxy / NAT", (x0 + 124, 655), "start"))

    s.append(legend(30, Hh - 14, [("req", "request / data path"), ("vpn", "Site-to-Site VPN (IPsec) / Interconnect")]))
    return svg(W, Hh, s, "mcp-stock-server deployment architecture: the same image runs (a) on Agent Runtime behind a Public "
               "MCP Gateway, (b) on vServer / VKS in the customer VPC behind a Private gateway, or (c) on-premises behind a "
               "Private gateway with Route CIDRs; in every case the server calls the public 24hMoney API")


def a_stock_flow():
    """sample-mcp-stock-server call flow on one straight row:
    Agent -> MCP Gateway (connector stock, API key) -> vn-stock-mcp -> 24hMoney."""
    W, Hh, m, GX = 1500, 490, 260, 220
    s = []
    s.append(group(20, 20, 1160, 440, "AgentBase Platform — managed by GreenNode", "managed"))
    s.append(node(60, m - 24, "agent-runtime", C["compute"], "Agent", ["travel-buddy · your agents"]))
    mids = gateway_pipeline(s, GX, m, ["stock"], "Network: Public", "MCP Gateway · Public")
    cx = GX + 212 + (490 - 222) // 2
    s.append(node(cx - 24, 50, "access-control", C["idc"], "Access Control", ["secret: stock-mcp-key"]))
    s.append(group(760, 130, 400, 310, "Agent Runtime · vn-stock-mcp", "shared"))
    s.append(node(800, m - 24, "inbound-auth", C["net"], "API key check", ["fail-closed · 401 / 503"]))
    s.append(node(980, m - 24, "mcp", C["mcp"], "13 MCP tools", ["/mcp · FastMCP"]))
    s.append(card(780, 330, 360, 92, "Tools", [
        "Market: top · gainers · losers · active · quote",
        "Company: search · profile · valuation",
        "History: price · foreign · dividend · plan · news",
    ]))
    s.append(group(1210, 20, 270, 440, "Internet", "internet"))
    s.append(node(1310, m - 24, "globe", C["gray"], "24hMoney API", ["public · unofficial"]))

    s.append(arrow([(108, m), (GX + 24, m)], "req", "tools/call", (164, m - 8)))
    s.append(arrow([(cx, 136), (cx, m - 28)], "req", "API key", (cx - 10, 170), "end"))
    s.append(arrow([(GX + 480, m), (800, m)], "req", "X-Api-Key", (750, m - 8)))
    s.append(arrow([(848, m), (980, m)], "req", "OK", (914, m - 8)))
    s.append(arrow([(1028, m), (1310, m)], "req", "HTTPS", (1250, m - 8)))
    s.append(steps_svg([[(164, m + 16)], [(cx, 150)], [(750, m + 16)], [(914, m + 16)], [(1120, m + 16)]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path")]))
    return svg(W, Hh, s, "mcp-stock-server architecture: agents call tools through a Public MCP Gateway; the stock connector "
               "attaches an API key from Access Control; the server validates the key and calls the 24hMoney API")


def a_byo():
    """sample-byo-agent-mcp-gateway: an agent or app outside AgentBase -> Public MCP Gateway -> MCP servers.
    The two callers merge on a bus that enters the gateway on its row; each connector leaves straight."""
    W, Hh, ROW, GX = 1500, 500, 240, 450
    s = []
    s.append(group(20, 60, 330, 400, "Your infrastructure (outside GreenNode)", "onprem"))
    s.append(node(80, ROW - 104, "app", C["app"], "Your agent / app", ["LangGraph · script · CLI"]))
    s.append(node(80, ROW + 56, "app", C["app"], "Claude Desktop / Cursor", ["via mcp-remote"]))
    s.append(group(400, 20, 750, 460, "AgentBase Platform — managed by GreenNode", "managed"))
    mids = gateway_pipeline(s, GX, ROW, ["tavily", "stock"], "Network: Public", "MCP Gateway · Public")
    cx = GX + 212 + (490 - 222) // 2
    s.append(node(cx - 24, 36, "access-control", C["idc"], "Access Control", ["connector secrets"]))
    s.append(node(1000, mids["stock"] - 24, "mcp", C["mcp"], "MCP server", ["on Agent Runtime"]))
    s.append(group(1180, 60, 300, 400, "Internet", "internet"))
    s.append(node(1250, mids["tavily"] - 24, "mcp", C["mcp"], "MCP SaaS", ["Tavily · GitHub …"]))
    s.append(card(450, 370, 560, 90, "Note", [
        "External callers need a Public gateway. A Private gateway is only",
        "reachable from the customer private network.",
    ]))

    # 1 · both callers -> one bus -> the gateway row
    s.append(line([(128, ROW - 80), (380, ROW - 80)]))
    s.append(line([(128, ROW + 80), (380, ROW + 80)]))
    s.append(f'<polyline points="380,{ROW - 80} 380,{ROW + 80}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    s.append(arrow([(380, ROW), (GX + 24, ROW)], "req"))
    s.append(text(254, ROW - 88, "HTTPS · IAM token / JWT", 10.5, 600, INK, "middle", halo=True))
    s.append(arrow([(cx, 122), (cx, mids["tavily"] - 28)], "req"))   # secrets for the connectors
    s.append(arrow([(GX + 480, mids["tavily"]), (1250, mids["tavily"])], "req"))
    s.append(arrow([(GX + 480, mids["stock"]), (1000, mids["stock"])], "req"))
    s.append(steps_svg([[(380, ROW - 40)], [(420, ROW)], [(cx, 160)], [(1110, mids["tavily"])], [(965, mids["stock"])]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path")]))
    return svg(W, Hh, s, "BYO agent architecture: an agent or app running outside AgentBase calls a Public MCP Gateway with an IAM "
               "token or JWT; the gateway checks policy, takes connector secrets from Access Control and calls the MCP servers")


def a_onprem():
    """sample-onprem-mcp-vpn: Agent -> Private MCP Gateway -> customer VPC -> VPN Site-to-Site -> on-prem MCP.
    The whole request path is one straight row; LLM sits straight above the Agent, Access Control straight above
    the connector."""
    W, Hh, e, AX, GX = 1760, 590, 360, 282, 420
    s = []
    cx = GX + 212 + (490 - 222) // 2
    s.append(group(20, 20, 940, 530, "AgentBase Platform — managed by GreenNode", "managed"))
    svc = shared_services(s, AX + 24 - 64, 56, 3 * ((cx - AX - 24) // 2) + 40)
    s.append(abvpc(36, 230, 908, 300))
    s.append(node(AX, e - 24, "agent-runtime", C["compute"], "Agent", ["Agent Runtime"]))
    mids = gateway_pipeline(s, GX, e, ["erp@onprem"], "Network: Private · Route CIDRs = on-prem", "MCP Gateway · Private")
    s.append(group(990, 20, 360, 530, "Customer VPC on GreenNode · 10.20.0.0/16", "vpc"))
    s.append(node(1146, e - 24, "gn-vnet", C["net"], "VPN Site-to-Site", ["GreenNode vNetwork"]))
    s.append(card(1010, 70, 320, 95, "Route table (example)", [
        ("10.20.0.0/16", "local"),
        ("172.30.0.0/16", "→ AgentBase (private)"),
        ("192.168.0.0/16", "→ VPN Site-to-Site"),
    ]))
    s.append(group(1380, 20, 360, 530, "Customer data center · 192.168.0.0/16", "onprem"))
    s.append(node(1420, e - 24, "firewall", C["gray"], "IPsec gateway", ["strongSwan / firewall"]))
    s.append(node(1640, e - 24, "mcp", C["mcp"], "onprem-mcp", ["API key · audit log"]))
    s.append(node(1640, 460, "db", C["gray"], "ERP · HR · Inventory", ["stays on-premises"]))
    s.append(card(1400, 70, 320, 139, "DC routes + firewall (example)", [
        ("10.20.0.0/16", "→ IPsec tunnel"),
        ("172.30.0.0/16", "→ IPsec tunnel"),
        ("src 10.20.0.0/16", "allow tcp/8443"),
        ("src 172.30.0.0/16", "allow tcp/8443"),
        ("udp 500/4500, ESP", "VPN peer only"),
        ("everything else", "deny"),
    ]))

    # 1 · Agent -> LLM (straight up) and Memory
    to_services(s, [(AX + 24, e - 24), (AX + 24, 206)], 206, [svc["llm"], svc["memory"]], 56 + SVC_TIP)
    s.append(text(AX + 14, 290, "LLM · Memory", 10.5, 600, INK, "end", halo=True))
    # 2 · MCP tools/call, on the Agent's row
    s.append(arrow([(AX + 48, e), (GX + 24, e)], "req", "tools/call", ((AX + 48 + GX) // 2 + 6, e - 8)))
    # 3 · Access Control -> connector, straight down: the outbound API key
    s.append(arrow([(cx, 186), (cx, e - 28)], "req", "API key", (cx - 10, 254), "end"))
    s.append(arrow([(GX + 480, e), (1146, e)], "req", "private", (1040, e - 8)))
    s.append(arrow([(1194, e), (1420, e)], "vpn", both=True))
    s.append(text(1307, e - 10, "IPsec IKEv2 tunnel", 10.5, 600, C["sec"], "middle", halo=True))
    s.append(text(1307, e + 22, "over the Internet", 10.5, 400, C["sec"], "middle", halo=True))
    s.append(arrow([(1468, e), (1640, e)], "req", "tcp/8443", (1554, e - 8)))
    s.append(arrow([(1664, e + 66), (1664, 460)], "req", "SQL", (1680, 436), "start"))
    s.append(steps_svg([[(AX + 24, 260)], [((AX + 48 + GX) // 2 + 6, e + 16)], [(cx, 220)], [(1040, e + 16)],
                        [(1307, e + 40)], [(1554, e + 16)], [(1664, 432)]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path"), ("vpn", "Site-to-Site VPN (IPsec)")]))
    return svg(W, Hh, s, "On-premises MCP architecture: an agent on AgentBase uses the platform LLM and Memory and calls a "
               "Private MCP Gateway, whose connector takes its API key from Access Control and reaches the on-premises MCP "
               "server through the customer VPC and GreenNode VPN Site-to-Site")


ROOT = OUT.parents[2]          # the sample-repos folder that holds the sibling repos
ARCH_JOBS = [("sample-travel-buddy", "architecture.svg", a_travel),
             ("sample-zalo-restaurant", "architecture.svg", a_zalo),
             ("sample-mcp-stock-server", "architecture.svg", a_stock),
             ("sample-mcp-stock-server", "call-flow.svg", a_stock_flow),
             ("sample-byo-agent-mcp-gateway", "architecture.svg", a_byo),
             ("sample-onprem-mcp-vpn", "architecture.svg", a_onprem)]


JOBS = [("01-connectivity-map.svg", d1), ("02-uc-public.svg", d_public), ("03-uc-private-cloud.svg", d2),
        ("04-uc-hybrid-onprem.svg", d3), ("05-onprem-connectivity.svg", d4)]

if __name__ == "__main__":
    for name, fn in JOBS:
        (OUT / name).write_text(fn(), encoding="utf-8")
        print("✓", name)
    for repo, name, fn in ARCH_JOBS:    # diagrams of each sample repo
        d = ROOT / repo / "docs"
        if d.parent.is_dir():
            d.mkdir(exist_ok=True)
            (d / name).write_text(fn(), encoding="utf-8")
            print("✓", f"{repo}/docs/{name}")
