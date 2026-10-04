#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentBase network diagrams in the AWS Architecture Diagram style.

Writes five SVG files next to this script (they render directly in GitHub READMEs):
  01-connectivity-map.svg    overview: Agent Runtime + vCR, LLM / Memory / Access Control, MCP Gateways + connectors
  02-uc-public.svg           Public use case: Runtime in Public mode, Public gateway, MCP on the Internet and on AgentBase
  03-uc-private-cloud.svg    use case A: Private runtime and gateway, MCP servers in the customer VPC (no Internet)
  04-uc-hybrid-onprem.svg    use case B: Private runtime and gateway, MCP servers in the customer data center
  05-onprem-connectivity.svg on-premises <-> customer VPC connectivity (VPN / Interconnect, routes, firewall, CIDRs)
It also writes docs/architecture.svg of each sibling sample repo (ARCH_JOBS).

Model (GreenNode AgentBase documentation):
  - Agent Runtime and MCP Gateway are managed by GreenNode on the AgentBase Platform, never inside the customer
    VPC. Public mode uses AgentBase's shared public endpoint; Private mode runs in the AgentBase VPC
    (172.30.0.0/16), which is connected privately to the customer VPC. On-premises networks join the customer
    VPC over VPN Site-to-Site or Interconnect.
  - Every MCP tool call goes Agent -> MCP Gateway (Inbound Auth -> Policy Group) -> MCP Connector (URL +
    Outbound Auth, secret from Access Control) -> MCP server. A Private gateway only uses the private network,
    so MCP servers on the Internet sit behind a separate Public gateway.
  - LLM calls are a separate path: directly to the AI Platform or through the Sidecar LLM Proxy (localhost:18080).

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


def node(x, y, glyph, color, name, sub=(), side="below", badge=None):
    return icon(x, y, glyph, color, badge) + (lbl_below(x, y, name, sub) if side == "below" else lbl_right(x, y, name, sub))


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


def gateway_block(s, names, network, title, x=680, y=236):
    """MCP Gateway (managed by GreenNode): Inbound Auth -> Policy Group -> MCP Connectors."""
    w, h = 450, 304
    s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#F8F7FC" stroke="{C["net"]}" stroke-width="1.5"/>')
    s.append(f'<rect x="{x}" y="{y}" width="26" height="26" fill="#FFFFFF" stroke="{C["net"]}" stroke-width="1.5"/>'
             + use("mcp-gateway", x + 4, y + 4, 18))
    s.append(text(x + 34, y + 18, title, 12.5, 700, C["net"]))
    s.append(text(x + w - 10, y + 18, "MCP Connectors", 11, 700, INK, "end"))
    s.append(node(x + 26, y + 40, "inbound-auth", C["net"], "Inbound Auth", ["IAM Permissions", "JWT (default)"]))
    s.append(node(x + 26, y + 170, "policy", C["idc"], "Policy Group", ["ALLOW / DENY"]))
    s.append(arrow([(x + 50, y + 138), (x + 50, y + 170)], "req"))
    s.append(text(x + 12, y + h - 10, network, 10.5, 600, C["net"]))
    mids = {}
    for i, n in enumerate(names):
        url, auth = CONNECTORS[n]
        cy = y + 26 + i * 62
        s.append(connector(x + 120, cy, n, url, auth))
        mids[n] = cy + 28
    bus = x + 106
    s.append(f'<polyline points="{x+74},{y+194} {bus},{y+194}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    lo, hi = min(list(mids.values()) + [y + 194]), max(list(mids.values()) + [y + 194])
    s.append(f'<polyline points="{bus},{lo} {bus},{hi}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    for m in mids.values():
        s.append(arrow([(bus, m), (x + 120, m)], "req"))
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
def scene(show, conns, title):
    has = lambda k: k in show
    two_gw = has("internet")                 # overview: a Public and a Private gateway
    DY = 80 if two_gw else 0                 # with two gateways everything under the AgentBase VPC moves down
    SH = 0 if two_gw else 40                 # one gateway: room between Shared services and the AgentBase VPC
    RY = 520 + SH                            # top of the Agent Runtime tile
    W = 1680 if has("onprem") else 1350
    top = 0 if has("internet") else 110
    Hh = 1340 + DY + SH - top
    s = []
    if has("internet"):
        s.append(group(20, 16, W - 40, 94, "Internet", "internet"))
        s.append(node(90, 40, "registry", C["gray"], "Public registry", ["alternative to vCR (opt-in)", "Docker Hub · GHCR …"], "right"))
        s.append(node(1330, 40, "mcp", C["mcp"], "MCP servers on the Internet", ["Tavily · GitHub · Slack …"], "right"))

    s.append(group(20, 130, 1320, 1170 + DY + SH, "GreenNode Cloud", "cloud"))
    s.append(group(34, 166, 1292, 1120 + DY + SH, "Region HCM", "region"))
    s.append(group(50, 200, 1260, (670 if two_gw else 600), "AgentBase Platform — managed by GreenNode", "managed"))
    s.append(node(90, 250, "gn-cr", C["compute"], "Container Registry", ["vCR"]))
    svc = shared_services(s, 180, 236, 460)
    # the Public gateway is AgentBase's shared public endpoint, so it is drawn outside the AgentBase VPC
    s.append(abvpc(70, 450, 1220, 410) if two_gw else abvpc(70, 440, 1220, 350))
    s.append(node(400, RY, "agent-runtime", C["compute"], "Agent Runtime", ["sidecar LLM :18080"]))
    private_net = ("Network: Private · Route CIDRs = on-prem CIDR" if has("onprem")
                   else "Network: Private → customer VPC")
    if two_gw:
        mids = gateway_compact(s, 680, 236, "MCP Gateway · Public", [n for n in conns if n in ("tavily", "stock")],
                               "Network: Public (shared public endpoint)")
        mids.update(gateway_compact(s, 680, 636, "MCP Gateway · Private",
                                    [n for n in conns if n not in ("tavily", "stock")], private_net))
        gw_entries = [294, 694]
    else:
        mids = gateway_block(s, conns, private_net, "MCP Gateway · Private", y=420 + SH)
        gw_entries = [484 + SH]
    if has("hosted"):
        s.append(node(1156, mids["stock"] - 24, "mcp", C["mcp"], "MCP server", ["on Agent Runtime"]))

    L = 830 + DY + SH                        # top of the customer VPC
    s.append(group(50, L, 1260, 440, "Customer VPC · xx.xx.x.x/xx", "vpc"))
    s.append(group(70, L + 40, 450, 150, "Private subnet · app", "private"))
    s.append(node(300, L + 80, "app", C["app"], "Internal app", ["customer internal system"]))
    rows = [("xx.xx.x.x/xx", "local (VPC)"), ("172.30.0.0/16", "→ AgentBase (private)")]
    if has("onprem"):
        rows += [("xx.xx.x.x/xx", "on-prem → VPN GW / Interconnect")]
    s.append(card(70, L + 220, 450, 30 + 17 * len(rows) + 10, "Route table (customer VPC)", rows))
    if has("vpcmcp"):
        s.append(group(760, L + 40, 330, 270, "Private subnet · MCP", "private"))
        s.append(node(960, L + 100, "mcp", C["mcp"], "mcp-crm", ["vServer"], badge="gn-server"))
        s.append(node(800, L + 200, "gn-vdb", C["db"], "vDB", ["database"]))
        if "inventory" in conns:
            s.append(node(960, L + 200, "mcp", C["mcp"], "mcp-inventory", ["VKS"], badge="gn-vks"))
    if has("onprem"):
        s.append(node(1231, L + 300, "gn-vnet", C["net"], "VPN GW", ["/ Interconnect"]))
        s.append(group(1370, L, 290, 440, "On-premises · xx.xx.x.x/xx", "onprem"))
        s.append(node(1420, L + 300, "firewall", C["gray"], "Firewall",
                      ["allow + return route", "VPC CIDR · 172.30.0.0/16"]))
        s.append(node(1570, L + 190, "mcp", C["mcp"], "mcp-erp", ["xx.xx.x.x"]))
        if "hr" in conns:
            s.append(node(1570, L + 340, "mcp", C["mcp"], "mcp-hr", ["xx.xx.x.x"]))

    # ── flows: no line crosses a group title or a label ──
    steps = []
    # 1 · an internal app invokes the agent over the private connection
    s.append(arrow([(324, L + 80), (324, RY + 36), (400, RY + 36)], "req", ["invoke", "(private)"], (334, RY + 180), "start"))
    steps.append([(324, RY + 130)])
    # the image is pulled at deploy time, not per request, so this arrow has no step number
    s.append(arrow([(114, 345), (114, 418), (412, 418), (412, RY)], "req", "pull image (at deploy)", (240, 412)))
    # 2 · Agent -> LLM, Memory and Access Control, one arrow per service
    bus = 386
    to_services(s, [(436, RY), (436, bus)], bus, [svc["llm"], svc["memory"], svc["ac"]], 236 + SVC_TIP)
    ly = 410 if two_gw else 470
    s.append(text(446, ly, "LLM (direct or sidecar :18080)", 10.5, 600, INK, "start", halo=True)
             + text(446, ly + 13, "Memory · Access Control", 10.5, 600, INK, "start", halo=True))
    steps.append([(436, RY - 30)])
    # 3 · MCP tools/call -> gateway(s)
    if two_gw:
        s.append(f'<polyline points="448,{RY + 24} 660,{RY + 24}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
        s.append(f'<polyline points="660,{gw_entries[0]} 660,{gw_entries[1]}" fill="none" stroke="{INK}" stroke-width="1.6"/>')
        for ey in gw_entries:
            s.append(arrow([(660, ey), (694, ey)], "req"))
        s.append(text(554, RY + 16, "MCP tools/call", 10.5, 600, INK, "middle", halo=True))
        steps.append([(660, 600)])
    else:
        s.append(arrow([(448, RY + 24), (660, RY + 24), (660, gw_entries[0]), (706, gw_entries[0])], "req",
                       ["MCP tools/call"], (554, RY + 16)))
        steps.append([(660, gw_entries[0] + 36)])
    if has("internet"):
        t = mids["tavily"]
        s.append(arrow([(1120, t), (1150, t), (1150, 64), (1330, 64)], "req"))
        steps.append([(1150, 300)])
    if has("hosted"):
        st = mids["stock"]
        s.append(arrow([(1120, st), (1156, st)], "req"))
        steps.append([(1138, st)])
    if has("vpcmcp"):
        c = mids["crm"]
        s.append(arrow([(1120, c), (1240, c), (1240, L + 124), (1008, L + 124)], "req"))
        s.append(arrow([(960, L + 124), (824, L + 124), (824, L + 200)], "req"))
        badges = [(1240, L + 40)]
        if "inventory" in conns:
            iv = mids["inventory"]
            s.append(arrow([(1120, iv), (1255, iv), (1255, L + 224), (1008, L + 224)], "req"))
            s.append(arrow([(960, L + 224), (848, L + 224)], "req"))
            badges.append((1255, L + 170))
        s.append(text(1120, L + 116, "private IP", 10.5, 600, INK, "middle", halo=True))
        steps.append(badges)
    if has("onprem"):
        e = mids["erp"]
        s.append(arrow([(1120, e), (1265, e), (1265, L + 300)], "req"))
        badges = [(1265, L + 230)]
        if "hr" in conns:
            h_ = mids["hr"]
            s.append(arrow([(1120, h_), (1245, h_), (1245, L + 300)], "req"))
            badges.append((1245, L + 150))
        s.append(arrow([(1279, L + 316), (1420, L + 316)], "dx", both=True))
        s.append(arrow([(1279, L + 332), (1420, L + 332)], "vpn", both=True))
        s.append(arrow([(1468, L + 316), (1520, L + 316), (1520, L + 214), (1570, L + 214)], "req"))
        if "hr" in conns:
            s.append(arrow([(1468, L + 332), (1520, L + 332), (1520, L + 364), (1570, L + 364)], "req"))
        steps.append(badges)
    s.append(steps_svg(steps))

    lg = [("req", "request / data path")]
    if has("onprem"):
        lg += [("dx", "Interconnect / leased line"), ("vpn", "Site-to-Site VPN (IPsec)")]
    body = f'<g transform="translate(0 {-top})">{"".join(s)}</g>' + legend(30, Hh - 16, lg)
    return svg(W, Hh, [body], title)


def d1():
    return scene({"internet", "hosted", "vpcmcp", "onprem"}, ["tavily", "stock", "erp", "crm"],
                 "AgentBase connectivity map: Agent Runtime in the AgentBase VPC calls a Public MCP Gateway (Internet MCP, MCP on "
                 "Agent Runtime) and a Private MCP Gateway (MCP in the customer VPC, MCP on-premises via VPN / Interconnect)")


def d2():
    return scene({"vpcmcp"}, ["inventory", "crm"],
                 "Use case A: a Private MCP Gateway calls MCP servers in the customer VPC through connectors, without Internet access")


def d3():
    return scene({"onprem"}, ["erp", "hr"],
                 "Use case B: a Private MCP Gateway calls on-premises MCP servers via VPN or Interconnect")


# ═════════════════════════ Public use case: Agent Runtime in PUBLIC mode ═══════════
# Internet users call the Runtime's public endpoint; the gateway is Public; MCP on the Internet and on AgentBase.
def d_public():
    OX = 170                                 # Internet column on the left (users)
    W, Hh = 1430 + OX, 800
    s = []
    s.append(group(20, 16, W - 40, 94, "Internet", "internet"))
    s.append(group(20, 130, 140, 630, "Internet", "internet"))
    s.append(people(60, 600) + lbl_below(60, 600, "Users / apps", ["browser · webhook", "A2A client"]))

    b = []
    b.append(node(1140, 40, "mcp", C["mcp"], "MCP servers on the Internet", ["Tavily · GitHub · Slack …"], "right"))
    b.append(group(20, 130, 1240, 630, "GreenNode Cloud", "cloud"))
    b.append(group(34, 166, 1212, 580, "Region HCM", "region"))
    b.append(group(50, 200, 1180, 530, "AgentBase Platform — managed by GreenNode", "managed"))
    b.append(node(90, 260, "gn-cr", C["compute"], "Container Registry", ["vCR"]))
    svc = shared_services(b, 180, 236, 460)
    mids = gateway_block(b, ["tavily", "github", "stock"], "Network: Public", "MCP Gateway · Public")
    b.append(node(1160, mids["stock"] - 24, "mcp", C["mcp"], "MCP server", ["on Agent Runtime"]))
    b.append(node(300, 600, "agent-runtime", C["compute"], "Agent Runtime", ["PUBLIC mode · sidecar LLM :18080"]))
    b.append(card(700, 580, 430, 130, "PUBLIC mode", [
        ("Runtime endpoint", "public HTTPS (IAM / JWT)"),
        ("Gateway network", "Public"),
        ("VPC / on-prem", "not required"),
        ("Best for", "demos, MCP SaaS, public tools"),
    ]))

    steps = []
    # the image is pulled at deploy time: no step number
    b.append(arrow([(114, 345), (114, 580), (312, 580), (312, 600)], "req", "pull image (at deploy)", (124, 440), "start"))
    # 2 · Agent -> LLM, Memory and Access Control, one arrow per service
    to_services(b, [(336, 600), (336, 390)], 390, [svc["llm"], svc["memory"], svc["ac"]], 236 + SVC_TIP)
    b.append(text(346, 450, "LLM (direct or sidecar :18080)", 10.5, 600, INK, "start", halo=True)
             + text(346, 463, "Memory · Access Control", 10.5, 600, INK, "start", halo=True))
    steps.append([(336, 520)])
    b.append(arrow([(348, 624), (660, 624), (660, 300), (706, 300)], "req", ["MCP tools/call", "→ MCP Gateway"], (650, 560), "end"))
    steps.append([(660, 600)])
    b.append(arrow([(1120, mids["tavily"]), (1150, mids["tavily"]), (1150, 88)], "req"))
    steps.append([(1150, 200)])
    b.append(arrow([(1120, mids["github"]), (1170, mids["github"]), (1170, 88)], "req"))
    steps.append([(1170, 260)])
    b.append(arrow([(1120, mids["stock"]), (1160, mids["stock"])], "req"))
    steps.append([(1142, mids["stock"] + 22)])

    s.append(f'<g transform="translate({OX} 0)">{"".join(b)}</g>')
    # 1 · users -> the Runtime's public endpoint (absolute coordinates)
    s.append(arrow([(108, 624), (OX + 300 - PAD, 624)], "req", "HTTPS · public endpoint", (300, 616)))
    s.append(step(300, 640, 1))
    s.append(f'<g transform="translate({OX} 0)">{steps_svg(steps, 2)}</g>')
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

    # middle: the two options
    b.append(text(680, 52, "Choose one (or both for redundancy)", 12, 700, INK, "middle"))
    b.append(f'<rect x="530" y="76" width="300" height="220" rx="6" fill="#FDF2F3" stroke="{C["sec"]}" stroke-width="1"/>')
    b.append(text(546, 100, "A · Site-to-Site VPN (IPsec)", 12, 700, C["sec"]))
    b.append(icon(656, 116, "globe", C["gray"]))
    b.append(text(680, 182, "Internet", 10.5, 600, SLATE, "middle"))
    for r, t in enumerate(["IKEv2 · 1 tunnel per on-prem CIDR", "static routes in the VPC route table",
                           "quick to set up, low cost", "bandwidth / latency depend on the Internet"]):
        b.append(text(546, 210 + r * 17, "· " + t, 10.5, 400, SLATE))
    b.append(f'<rect x="530" y="316" width="300" height="200" rx="6" fill="#FEF5EC" stroke="{C["compute"]}" stroke-width="1"/>')
    b.append(text(546, 340, "B · Interconnect / leased line", 12, 700, C["compute"]))
    for r, t in enumerate(["dedicated link DC ↔ GreenNode", "does not traverse the Internet", "fixed bandwidth + SLA",
                           "banking, sensitive data", "pair with VPN as backup"]):
        b.append(text(546, 368 + r * 17, "· " + t, 10.5, 400, SLATE))

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

    # links
    b.append(arrow([(428, 252), (500, 252), (500, 140), (656, 140)], "vpn", both=True))
    b.append(arrow([(704, 140), (860, 140), (860, 252), (900, 252)], "vpn", both=True))
    b.append(arrow([(428, 270), (510, 270), (510, 306), (850, 306), (850, 270), (900, 270)], "dx", both=True))

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
# Each diagram is written to docs/architecture.svg of the matching sibling repo.
def a_travel():
    """sample-travel-buddy: web users -> Runtime (UI + LangGraph) -> LLM / Memory; Tavily through a Public MCP Gateway."""
    W, Hh = 1500, 470
    s = []
    s.append(group(20, 60, 240, 380, "Internet", "internet"))
    s.append(people(116, 200) + lbl_below(116, 200, "Web users", ["Chat UI · REST · A2A"]))
    s.append(group(290, 20, 920, 430, "AgentBase Platform — managed by GreenNode", "managed"))
    s.append(node(340, 60, "gn-ai", C["ai"], "LLM — AI Platform", ["direct or sidecar :18080"]))
    s.append(node(500, 60, "memory", C["db"], "Memory", ["CUSTOM + SEMANTIC"]))
    s.append(node(870, 60, "access-control", C["idc"], "Access Control", ["secret: tavily-apikey"]))
    s.append(node(360, 200, "agent-runtime", C["compute"], "travel-buddy", ["Agent Runtime · Public", "UI + LangGraph"]))
    mids = gateway_compact(s, 600, 200, "MCP Gateway · Public", ["tavily"], "Network: Public")
    t = mids["tavily"]
    s.append(group(1240, 60, 240, 380, "Internet", "internet"))
    s.append(node(1300, t - 24, "mcp", C["mcp"], "Tavily MCP", ["web search · extract"]))

    s.append(arrow([(164, 224), (360, 224)], "req", "HTTPS", (200, 216)))
    to_services(s, [(384, 200), (384, 170)], 170, [364, 524], 146)
    s.append(arrow([(408, 224), (560, 224), (560, 258), (614, 258)], "req", "tools/call", (484, 216)))
    s.append(arrow([(894, 146), (894, 226)], "req", "API key", (906, 190), "start"))
    s.append(arrow([(1040, t), (1300, t)], "req", "HTTPS · API key", (1150, t - 8)))
    s.append(steps_svg([[(200, 240)], [(444, 170)], [(560, 240)], [(894, 168)], [(1150, t + 16)]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path")]))
    return svg(W, Hh, s, "travel-buddy architecture: web users call the Agent Runtime; the agent uses the LLM and Memory, "
               "and Tavily through a Public MCP Gateway whose connector takes its API key from Access Control")


def a_zalo():
    """sample-zalo-restaurant: Zalo -> public webhook proxy -> Private Agent Runtime -> Private MCP Gateway ->
    MCP server in the customer VPC; replies through the Zalo Bot API; traces go to a private self-hosted
    Langfuse that admins open over a client-to-site VPN."""
    W, Hh = 1600, 890
    s = []
    s.append(group(20, 60, 230, 780, "Internet", "internet"))
    s.append(people(100, 90) + lbl_below(100, 90, "Customers", ["chat on Zalo"]))
    s.append(node(100, 250, "globe", C["gray"], "Zalo Bot Platform", ["webhook · Bot API"]))
    s.append(node(100, 735, "app", C["app"], "Admin", ["VPN client"]))

    s.append(group(270, 20, 1310, 840, "GreenNode Cloud", "cloud"))
    s.append(group(300, 60, 1250, 450, "AgentBase Platform — managed by GreenNode", "managed"))
    svc = shared_services(s, 320, 96, 420)
    s.append(abvpc(310, 266, 1230, 224))
    s.append(node(580, 320, "agent-runtime", C["compute"], "Agent Runtime",
                  ["Private mode", "agent image only", "IP allow: proxy"]))
    mids = gateway_compact(s, 900, 286, "MCP Gateway · Private", ["restaurant@vpc"], "Network: Private → customer VPC")
    r = mids["restaurant@vpc"]

    s.append(group(300, 530, 1250, 310, "Customer VPC · 10.20.0.0/16", "vpc"))
    s.append(group(320, 570, 290, 255, "Public subnet", "public"))
    s.append(node(520, 600, "gn-server", C["net"], "Webhook proxy", ["vServer · Caddy", "POST /webhook/zalo only"]))
    s.append(node(380, 735, "firewall", C["gray"], "Admin VPN", ["pfSense / OpenVPN"]))
    s.append(group(640, 570, 580, 255, "Private subnet · observability", "private"))
    s.append(node(760, 600, "langfuse", C["ai"], "Langfuse", ["vServer / VKS · :3000"]))
    s.append(node(760, 735, "db", C["gray"], "Langfuse storage", ["Postgres · ClickHouse · Redis · MinIO"]))
    s.append(card(880, 615, 320, 112, "Inbound rules (SG / Network ACL)", [
        ("proxy :443", "Internet (Zalo webhook)"),
        ("mcp :8443", "AgentBase source range"),
        ("langfuse :3000", "AgentBase range + VPN pool"),
        ("vpn :1194/udp", "admin IPs only"),
    ]))
    s.append(group(1250, 570, 285, 255, "Private subnet · MCP", "private"))
    s.append(node(1300, 615, "mcp", C["mcp"], "zalo-mcp-server", ["vServer / VKS"], badge="gn-server"))
    s.append(node(1300, 735, "db", C["gray"], "SQLite volume", ["menu · bookings"]))

    # 1 · the guest writes on Zalo
    s.append(arrow([(124, 175), (124, 250)], "req", "message", (134, 218), "start"))
    # 2 · Zalo calls the webhook on the public proxy (only POST /webhook/zalo is forwarded)
    s.append(arrow([(148, 286), (260, 286), (260, 624), (520, 624)], "req", "HTTPS webhook", (390, 618)))
    # 3 · proxy -> Private runtime endpoint (the runtime's IP allow-list admits only the proxy)
    s.append(arrow([(544, 600), (544, 356), (580, 356)], "req", "private", (536, 420), "end"))
    # 4 · Agent -> LLM and Memory, one arrow per service
    to_services(s, [(604, 320), (604, 240)], 240, [svc["llm"], svc["memory"]], 96 + SVC_TIP)
    s.append(text(614, 300, "LLM · Memory", 10.5, 600, INK, "start", halo=True))
    # 5 · MCP tools/call -> Private gateway
    s.append(arrow([(628, 344), (914, 344)], "req", "MCP tools/call", (770, 338)))
    # 6 · Access Control -> connector: the API key, never seen by the agent
    s.append(arrow([(660, 152), (1180, 152), (1180, 312)], "req", "API key", (1190, 250), "start"))
    # 7 · connector -> MCP server in the customer VPC
    s.append(arrow([(1350, r), (1500, r), (1500, 639), (1348, 639)], "req", "HTTPS :8443 · X-Api-Key", (1490, 470), "end"))
    # 8 · reply: the agent calls the Zalo Bot API (sendMessage) over HTTPS
    s.append(arrow([(580, 334), (280, 334), (280, 262), (148, 262)], "req", "sendMessage (reply)", (450, 328)))
    # 9 · traces to the private Langfuse
    s.append(arrow([(620, 434), (620, 612), (760, 612)], "req", "traces (OTel)", (628, 465), "start"))
    # 10 · admins: client-to-site VPN, then the Langfuse UI on a private IP
    s.append(arrow([(148, 759), (380, 759)], "vpn", both=True))
    s.append(arrow([(428, 759), (720, 759), (720, 636), (760, 636)], "req", "Langfuse UI", (574, 753)))
    s.append(arrow([(784, 688), (784, 735)], "req"))
    s.append(arrow([(1324, 700), (1324, 735)], "req"))
    s.append(steps_svg([[(124, 205)], [(285, 624)], [(544, 450)], [(494, 240)], [(770, 360)], [(1180, 200)],
                        [(1500, 410)], [(350, 334)], [(690, 612)], [(205, 759)]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path"), ("vpn", "client-to-site VPN (admins)")]))
    return svg(W, Hh, s, "Zalo restaurant architecture: Zalo webhooks reach a Private Agent Runtime through a public proxy; "
               "the agent calls the MCP server in the customer VPC via a Private MCP Gateway, replies through the Zalo Bot "
               "API and sends traces to a private self-hosted Langfuse that admins open over a client VPN")


def a_stock():
    """sample-mcp-stock-server: Agent -> MCP Gateway (connector stock, API key) -> vn-stock-mcp -> 24hMoney."""
    W, Hh = 1500, 560
    s = []
    s.append(group(20, 20, 1200, 440, "AgentBase Platform — managed by GreenNode", "managed"))
    s.append(node(60, 215, "agent-runtime", C["compute"], "Agent", ["travel-buddy · your agents"]))
    s.append(node(560, 40, "access-control", C["idc"], "Access Control", ["secret: stock-mcp-key"]))
    mids = gateway_compact(s, 300, 150, "MCP Gateway · Public", ["stock"], "Network: Public")
    m = mids["stock"]
    s.append(group(820, 120, 380, 320, "Agent Runtime · vn-stock-mcp", "shared"))
    s.append(node(870, m - 24, "inbound-auth", C["net"], "API key check", ["fail-closed · 401 / 503"]))
    s.append(node(1030, m - 24, "mcp", C["mcp"], "13 MCP tools", ["/mcp · FastMCP"]))
    s.append(card(840, 300, 350, 120, "Tools", [
        "Market: top · gainers · losers · active · quote",
        "Company: search · profile · valuation",
        "History: price · foreign · dividend · plan · news",
    ]))
    s.append(group(1250, 20, 230, 440, "Internet", "internet"))
    s.append(node(1310, m - 24, "globe", C["gray"], "24hMoney API", ["public · unofficial"]))

    s.append(arrow([(108, 239), (250, 239), (250, 208), (314, 208)], "req", "tools/call", (180, 231)))
    s.append(arrow([(584, 125), (584, 176)], "req", "API key", (594, 142), "start"))
    s.append(arrow([(740, m), (870, m)], "req", "X-Api-Key", (785, m - 8)))
    s.append(arrow([(918, m), (1030, m)], "req", "OK", (974, m - 8)))
    s.append(arrow([(1078, m), (1310, m)], "req", "HTTPS", (1270, m - 8)))
    s.append(steps_svg([[(180, 255)], [(584, 160)], [(785, m + 16)], [(974, m + 16)], [(1150, m + 16)]]))
    # the same image runs in three places
    s.append(text(20, 500, "One image, three deployment targets:", 12, 700, INK))
    for x, ic, t1, t2 in [(290, "agent-runtime", "Agent Runtime", "gateway Public"),
                          (610, "gn-vks", "vServer / VKS in the customer VPC", "gateway Private"),
                          (1000, "building", "On-premises (VPN / Interconnect)", "gateway Private + Route CIDRs")]:
        s.append(icon(x, 472, ic, C["compute"] if ic != "building" else MUTED))
        s.append(text(x + 58, 492, t1, 11.5, 700, INK) + text(x + 58, 508, t2, 10.5, 400, SLATE))
    s.append(legend(30, Hh - 14, [("req", "request / data path")]))
    return svg(W, Hh, s, "mcp-stock-server architecture: agents call tools through a Public MCP Gateway; the stock connector "
               "attaches an API key from Access Control; the server validates the key and calls the 24hMoney API")


def a_byo():
    """sample-byo-agent-mcp-gateway: an agent or app outside AgentBase -> Public MCP Gateway -> MCP servers."""
    W, Hh = 1500, 500
    s = []
    s.append(group(20, 60, 330, 400, "Your infrastructure (outside GreenNode)", "onprem"))
    s.append(node(80, 120, "app", C["app"], "Your agent / app", ["LangGraph · script · CLI"]))
    s.append(node(80, 280, "app", C["app"], "Claude Desktop / Cursor", ["via mcp-remote"]))
    s.append(group(400, 20, 750, 460, "AgentBase Platform — managed by GreenNode", "managed"))
    s.append(node(760, 40, "access-control", C["idc"], "Access Control", ["connector secrets"]))
    mids = gateway_compact(s, 450, 160, "MCP Gateway · Public", ["tavily", "stock"], "Network: Public")
    s.append(node(1000, mids["stock"] - 24, "mcp", C["mcp"], "MCP server", ["on Agent Runtime"]))
    s.append(group(1180, 60, 300, 400, "Internet", "internet"))
    s.append(node(1250, mids["tavily"] - 24, "mcp", C["mcp"], "MCP SaaS", ["Tavily · GitHub …"]))
    s.append(card(450, 370, 560, 90, "Note", [
        "External callers need a Public gateway. A Private gateway is only",
        "reachable from the customer private network.",
    ]))

    s.append(f'<polyline points="128,144 380,144" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    s.append(f'<polyline points="128,304 380,304" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    s.append(f'<polyline points="380,144 380,304" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    s.append(arrow([(380, 218), (464, 218)], "req"))
    s.append(text(254, 136, "HTTPS · IAM token / JWT", 10.5, 600, INK, "middle", halo=True))
    s.append(arrow([(784, 126), (784, 160)], "req"))   # secrets for both connectors
    s.append(arrow([(890, mids["tavily"]), (1250, mids["tavily"])], "req"))
    s.append(arrow([(890, mids["stock"]), (1000, mids["stock"])], "req"))
    s.append(steps_svg([[(254, 160)], [(420, 218)], [(804, 143)], [(1110, mids["tavily"])], [(945, mids["stock"])]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path")]))
    return svg(W, Hh, s, "BYO agent architecture: an agent or app running outside AgentBase calls a Public MCP Gateway with an IAM "
               "token or JWT; the gateway checks policy, takes connector secrets from Access Control and calls the MCP servers")


def a_onprem():
    """sample-onprem-mcp-vpn: Agent -> Private MCP Gateway -> customer VPC -> VPN Site-to-Site -> on-prem MCP."""
    W, Hh, DY = 1600, 590, 170        # DY: room above the AgentBase VPC for Shared services
    s = []
    s.append(group(20, 20, 710, 530, "AgentBase Platform — managed by GreenNode", "managed"))
    svc = shared_services(s, 40, 56, 670)
    s.append(abvpc(36, 60 + DY, 678, 300))
    s.append(node(60, 200 + DY, "agent-runtime", C["compute"], "Agent", ["Agent Runtime"]))
    mids = gateway_compact(s, 220, 120 + DY, "MCP Gateway · Private", ["erp@onprem"], "Network: Private · Route CIDRs = on-prem")
    e = mids["erp@onprem"]
    s.append(group(760, 20, 400, 530, "Customer VPC on GreenNode · 10.20.0.0/16", "vpc"))
    s.append(node(910, e - 24, "gn-vnet", C["net"], "VPN Site-to-Site", ["GreenNode vNetwork"]))
    s.append(card(780, 70, 360, 95, "Route table (example)", [
        ("10.20.0.0/16", "local"),
        ("172.30.0.0/16", "→ AgentBase (private)"),
        ("192.168.0.0/16", "→ VPN Site-to-Site"),
    ]))
    s.append(group(1190, 20, 390, 530, "Customer data center · 192.168.0.0/16", "onprem"))
    s.append(node(1230, e - 24, "firewall", C["gray"], "IPsec gateway", ["strongSwan / firewall"]))
    s.append(node(1460, e - 24, "mcp", C["mcp"], "onprem-mcp", ["API key · audit log"]))
    s.append(node(1460, 460, "db", C["gray"], "ERP · HR · Inventory", ["stays on-premises"]))
    s.append(card(1200, 70, 240, 139, "DC routes + firewall (example)", [
        ("10.20.0.0/16", "→ IPsec tunnel"),
        ("172.30.0.0/16", "→ IPsec tunnel"),
        ("src 10.20.0.0/16", "allow tcp/8443"),
        ("src 172.30.0.0/16", "allow tcp/8443"),
        ("udp 500/4500, ESP", "VPN peer only"),
        ("everything else", "deny"),
    ]))

    # 1 · Agent -> LLM and Memory: around the AgentBase VPC title, then one arrow per service
    to_services(s, [(84, 200 + DY), (84, 272), (290, 272), (290, 206)], 206, [svc["llm"], svc["memory"]], 56 + SVC_TIP)
    s.append(text(300, 254, "LLM · Memory", 10.5, 600, INK, "start", halo=True))
    # 2 · MCP tools/call
    s.append(arrow([(108, 224 + DY), (180, 224 + DY), (180, 178 + DY), (234, 178 + DY)], "req", "tools/call", (144, 240 + DY)))
    # 3 · Access Control -> connector: the outbound API key
    s.append(arrow([(548, 186), (548, 146 + DY)], "req", "API key", (560, 254), "start"))
    s.append(arrow([(660, e), (910, e)], "req", "private", (840, e - 8)))
    s.append(arrow([(958, e), (1230, e)], "vpn", both=True))
    s.append(text(1094, e - 10, "IPsec IKEv2 tunnel", 10.5, 600, C["sec"], "middle", halo=True))
    s.append(text(1094, e + 22, "over the Internet", 10.5, 400, C["sec"], "middle", halo=True))
    s.append(arrow([(1278, e), (1460, e)], "req", "tcp/8443", (1369, e - 8)))
    s.append(arrow([(1484, e + 66), (1484, 460)], "req", "SQL", (1500, 436), "start"))
    s.append(steps_svg([[(187, 272)], [(180, 201 + DY)], [(548, 270)], [(800, e + 16)], [(1094, e + 40)],
                        [(1369, e + 16)], [(1484, 432)]]))
    s.append(legend(30, Hh - 14, [("req", "request / data path"), ("vpn", "Site-to-Site VPN (IPsec)")]))
    return svg(W, Hh, s, "On-premises MCP architecture: an agent on AgentBase uses the platform LLM and Memory and calls a "
               "Private MCP Gateway, whose connector takes its API key from Access Control and reaches the on-premises MCP "
               "server through the customer VPC and GreenNode VPN Site-to-Site")


ROOT = OUT.parents[2]          # the sample-repos folder that holds the sibling repos
ARCH_JOBS = [("sample-travel-buddy", a_travel), ("sample-zalo-restaurant", a_zalo),
             ("sample-mcp-stock-server", a_stock), ("sample-byo-agent-mcp-gateway", a_byo),
             ("sample-onprem-mcp-vpn", a_onprem)]


JOBS = [("01-connectivity-map.svg", d1), ("02-uc-public.svg", d_public), ("03-uc-private-cloud.svg", d2),
        ("04-uc-hybrid-onprem.svg", d3), ("05-onprem-connectivity.svg", d4)]

if __name__ == "__main__":
    for name, fn in JOBS:
        (OUT / name).write_text(fn(), encoding="utf-8")
        print("✓", name)
    for repo, fn in ARCH_JOBS:          # architecture diagram of each sample repo
        d = ROOT / repo / "docs"
        if d.parent.is_dir():
            d.mkdir(exist_ok=True)
            (d / "architecture.svg").write_text(fn(), encoding="utf-8")
            print("✓", repo + "/docs/architecture.svg")
