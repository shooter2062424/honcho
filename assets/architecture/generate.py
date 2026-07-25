#!/usr/bin/env python3
"""Generate the ARCHITECTURE.md diagrams as hand-tuned SVG.

Design notes
------------
* Every shape carries LIGHT-theme presentation attributes (fill/stroke) so the
  file renders correctly even if a sanitizer strips <style>.
* A single <style> block adds a `prefers-color-scheme: dark` override. CSS beats
  presentation attributes, so dark mode wins when the block survives.
* Text width is estimated CJK-aware (full-width glyphs ~1em, latin ~0.56em) so
  boxes are sized without a layout engine.
"""

from __future__ import annotations

import os
import unicodedata

OUT = os.path.dirname(os.path.abspath(__file__))

FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', "
    "'Noto Sans CJK TC', 'PingFang TC', 'Microsoft JhengHei', Helvetica, Arial, sans-serif"
)
if os.environ.get("VERIFY_FONT"):  # cairosvg has no font fallback; preview only
    FONT = "Noto Sans CJK TC, sans-serif"
MONO = "'SF Mono', 'JetBrains Mono', 'Fira Code', Consolas, 'Liberation Mono', monospace"

# (light fill, light stroke, light text) keyed by palette name
LIGHT = {
    "blue": ("#e8f1fd", "#2f6fd0", "#10315e"),
    "purple": ("#f1ecfd", "#7256c8", "#33246b"),
    "green": ("#e6f5ec", "#2c8459", "#14452e"),
    "amber": ("#fff3e2", "#c47a12", "#6b4106"),
    "gray": ("#f2f4f7", "#98a2b3", "#364152"),
    "rose": ("#fdeceb", "#cf4b3f", "#6f1f18"),
    "teal": ("#e4f4f6", "#2a8894", "#10454c"),
    "panel": ("#fbfcfe", "#d7dee8", "#5b6673"),
}
DARK = {
    "blue": ("#17233a", "#5b9bf0", "#cfe1ff"),
    "purple": ("#221b39", "#a087ea", "#e2d8ff"),
    "green": ("#12291d", "#4fb583", "#cdeedb"),
    "amber": ("#2e2109", "#e0a441", "#ffe6bd"),
    "gray": ("#1c222b", "#6b7684", "#d6dce4"),
    "rose": ("#2e1614", "#e5796b", "#ffd6d0"),
    "teal": ("#10262a", "#4fb6c2", "#c9eef2"),
    "panel": ("#12171f", "#2b3340", "#9198a1"),
}

CANVAS_L, CANVAS_D = "#ffffff", "#0d1117"
EDGE_L, EDGE_D = "#8b97a8", "#7d8896"
INK_L, INK_D = "#1c2530", "#e6edf3"
MUTE_L, MUTE_D = "#5b6673", "#9198a1"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cw(ch: str) -> float:
    """Relative width of one character in em units."""
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    if ch in "iljt.,:;'|!()[]":
        return 0.32
    if ch in "mwMW—…":
        return 0.85
    if ch.isupper():
        return 0.63
    return 0.545


def tw(s: str, size: float) -> float:
    return sum(cw(c) for c in s) * size


def style_block() -> str:
    rules = [
        "@media (prefers-color-scheme: dark){",
        f"  .canvas{{fill:{CANVAS_D}}}",
        f"  .ink{{fill:{INK_D}}} .mute{{fill:{MUTE_D}}}",
        f"  .edge{{stroke:{EDGE_D}}} .edge-fill{{fill:{EDGE_D}}}",
        f"  .lifeline{{stroke:{EDGE_D}}}",
    ]
    for name, (f, s, t) in DARK.items():
        rules.append(f"  .{name}-b{{fill:{f};stroke:{s}}}")
        rules.append(f"  .{name}-t{{fill:{t}}}")
        rules.append(f"  .{name}-s{{stroke:{s}}}")
    rules.append("}")
    return "<style>\n" + "\n".join(rules) + "\n</style>"


def defs() -> str:
    marks = []
    for name, color, cls in (("arw", EDGE_L, "edge-fill"),):
        marks.append(
            f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,1 L9,5 L0,9 z" fill="{color}" class="{cls}"/></marker>'
        )
        marks.append(
            f'<marker id="{name}-s" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M0,1 L9,5 L0,9 z" fill="{color}" class="{cls}"/></marker>'
        )
        marks.append(
            f'<marker id="{name}-open" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,1 L9,5 L0,9" fill="none" stroke="{color}" stroke-width="1.4" '
            f'class="edge"/></marker>'
        )
    return "<defs>\n" + "\n".join(marks) + "\n</defs>"


class Svg:
    def __init__(self, w: int, h: int, title: str):
        self.w, self.h, self.title = w, h, title
        self.body: list[str] = []

    # ---------- primitives ----------
    def rect(self, x, y, w, h, pal="gray", rx=9, dash=None, sw=1.4, op=None):
        f, s, _ = LIGHT[pal]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' fill-opacity="{op}"' if op else ""
        self.body.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{f}" '
            f'stroke="{s}" stroke-width="{sw}" class="{pal}-b"{d}{o}/>'
        )

    def text(self, x, y, s, size=13, pal=None, anchor="middle", weight="400",
             mono=False, cls=None, opacity=None):
        fill = LIGHT[pal][2] if pal else INK_L
        c = cls or (f"{pal}-t" if pal else "ink")
        fam = MONO if mono else FONT
        op = f' opacity="{opacity}"' if opacity else ""
        self.body.append(
            f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
            f'class="{c}"{op}>{esc(s)}</text>'
        )

    def line(self, x1, y1, x2, y2, dash=None, sw=1.5, marker="arw", cls="edge"):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = f' marker-end="url(#{marker})"' if marker else ""
        self.body.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{EDGE_L}" '
            f'stroke-width="{sw}" class="{cls}"{d}{m}/>'
        )

    def path(self, d, dash=None, sw=1.5, marker="arw", start_marker=None, cls="edge"):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        m = f' marker-end="url(#{marker})"' if marker else ""
        sm = f' marker-start="url(#{start_marker})"' if start_marker else ""
        self.body.append(
            f'<path d="{d}" fill="none" stroke="{EDGE_L}" stroke-width="{sw}" '
            f'class="{cls}"{da}{m}{sm}/>'
        )


    def elbow(self, pts, marker="arw", dash=None, sw=1.5, r=8):
        """Orthogonal polyline through pts with rounded corners."""
        d = f"M{pts[0][0]},{pts[0][1]}"
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
            dx0, dy0 = (x1 - x0), (y1 - y0)
            dx1, dy1 = (x2 - x1), (y2 - y1)
            l0 = max(abs(dx0), abs(dy0)); l1 = max(abs(dx1), abs(dy1))
            rr = min(r, l0 / 2, l1 / 2)
            ax = x1 - (rr if dx0 > 0 else -rr if dx0 < 0 else 0)
            ay = y1 - (rr if dy0 > 0 else -rr if dy0 < 0 else 0)
            bx = x1 + (rr if dx1 > 0 else -rr if dx1 < 0 else 0)
            by = y1 + (rr if dy1 > 0 else -rr if dy1 < 0 else 0)
            d += f" L{ax},{ay} Q{x1},{y1} {bx},{by}"
        d += f" L{pts[-1][0]},{pts[-1][1]}"
        self.path(d, marker=marker, dash=dash, sw=sw)

    def vline(self, x, y1, y2, marker="arw", dash=None, sw=1.5):
        self.line(x, y1, x, y2, marker=marker, dash=dash, sw=sw)

    # ---------- composites ----------
    def node(self, x, y, w, lines, pal="gray", rx=9, size=12.5, lead=16,
             pad=13, head_weight="600", sw=1.4, mono_from=99, dash=None):
        """Box with centred multi-line label. Returns (x, y, w, h)."""
        h = pad * 2 + lead * len(lines) - (lead - size) - 1
        self.rect(x, y, w, h, pal, rx=rx, sw=sw, dash=dash)
        ty = y + pad + size - 1
        for i, ln in enumerate(lines):
            self.text(
                x + w / 2, ty + i * lead, ln,
                size=size if i == 0 else size - 1.2,
                pal=pal,
                weight=head_weight if i == 0 else "400",
                mono=i >= mono_from,
                opacity=None if i == 0 else "0.86",
            )
        return (x, y, w, h)

    def diamond(self, cx, cy, w, h, lines, pal="amber", size=11.5, lead=14):
        f, s, _ = LIGHT[pal]
        self.body.append(
            f'<path d="M{cx},{cy - h / 2} L{cx + w / 2},{cy} L{cx},{cy + h / 2} '
            f'L{cx - w / 2},{cy} Z" fill="{f}" stroke="{s}" stroke-width="1.4" '
            f'class="{pal}-b"/>'
        )
        ty = cy - (len(lines) - 1) * lead / 2 + size / 2 - 1
        for i, ln in enumerate(lines):
            self.text(cx, ty + i * lead, ln, size=size, pal=pal,
                      weight="600" if i == 0 and len(lines) == 1 else "500")
        return (cx, cy, w, h)

    def panel(self, x, y, w, h, title, sub=None):
        self.rect(x, y, w, h, "panel", rx=13, dash="6 5", sw=1.3)
        self.text(x + 15, y + 22, title, size=12.5, pal="panel",
                  anchor="start", weight="700")
        if sub:
            self.text(x + 15, y + 38, sub, size=10.5, pal="panel",
                      anchor="start", mono=True, opacity="0.9")

    def elabel(self, x, y, s, size=10.5, anchor="middle", pad=4):
        """Edge label with a canvas-coloured plate so it stays legible."""
        w = tw(s, size) + pad * 2
        lx = {"middle": x - w / 2, "start": x - pad, "end": x - w + pad}[anchor]
        self.body.append(
            f'<rect x="{lx}" y="{y - size + 1}" width="{w}" height="{size + 5}" '
            f'rx="4" fill="{CANVAS_L}" class="canvas"/>'
        )
        self.text(x, y, s, size=size, anchor=anchor, cls="mute")

    def badge(self, cx, cy, n, r=8.5):
        self.body.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{LIGHT["gray"][0]}" '
            f'stroke="{LIGHT["gray"][1]}" stroke-width="1.2" class="gray-b"/>'
        )
        self.text(cx, cy + 3.4, str(n), size=9.5, pal="gray", weight="700")

    def render(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" role="img" '
            f'aria-label="{esc(self.title)}">\n'
            f"<title>{esc(self.title)}</title>\n"
            f"{style_block()}\n{defs()}\n"
            f'<rect width="{self.w}" height="{self.h}" fill="{CANVAS_L}" class="canvas"/>\n'
            + "\n".join(self.body)
            + "\n</svg>\n"
        )

    def save(self, name):
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            fh.write(self.render())
        print(f"  {name}  ({self.w}x{self.h})")


# =====================================================================
# sequence-diagram helper
# =====================================================================
class Seq(Svg):
    def __init__(self, title, participants, width_hint=1180):
        self.parts = participants  # list[(key, label, palette)]
        self.msgs: list[dict] = []
        self.title = title
        self.width_hint = width_hint

    def msg(self, a, b, lines, dashed=False, self_call=False):
        self.msgs.append(dict(kind="msg", a=a, b=b, lines=lines,
                              dashed=dashed, self_call=self_call))

    def note(self, a, b, text):
        self.msgs.append(dict(kind="note", a=a, b=b, lines=[text]))

    def loop(self, label, count):
        self.msgs.append(dict(kind="loop", label=label, count=count))

    def build(self):
        keys = [p[0] for p in self.parts]
        idx = {k: i for i, k in enumerate(keys)}
        n = len(keys)

        # --- head widths ---
        head_w = [max(112, tw(p[1], 12.5) + 30) for p in self.parts]

        # --- gap sizing driven by the widest label crossing each gap ---
        gaps = [0.0] * (n - 1)
        for m in self.msgs:
            if m["kind"] != "msg" or m["self_call"]:
                continue
            i, j = sorted((idx[m["a"]], idx[m["b"]]))
            need = max(tw(l, 10.8) for l in m["lines"]) + 34
            span = max(1, j - i)
            for g in range(i, j):
                gaps[g] = max(gaps[g], min(need / span, 268))
        for g in range(n - 1):
            gaps[g] = max(gaps[g], (head_w[g] + head_w[g + 1]) / 2 + 16, 132)

        xs = [86 + head_w[0] / 2]
        for g in range(n - 1):
            xs.append(xs[-1] + gaps[g])
        width = int(xs[-1] + head_w[-1] / 2 + 40)

        # --- vertical layout ---
        top = 92
        y = top + 26
        rows = []
        loop_stack = []
        closes: dict[int, bool] = {}
        for m in self.msgs:
            if m["kind"] == "loop":
                loop_stack.append((m["label"], m["count"], y - 12))
                closes[len(rows) + m["count"]] = True
                y += 30
                rows.append(("loopstart", m, y))
                continue
            if closes.pop(len(rows), False):
                y += 34
            if m["kind"] == "note":
                h = 34
                rows.append(("note", m, y + 6))
                y += h + 14
                continue
            nl = len(m["lines"])
            if m["self_call"]:
                y += 12 * nl + 22
                rows.append(("self", m, y))
                y += 20
            else:
                y += 13 * nl + 8
                rows.append(("msg", m, y))
                y += 16
        # close loop boxes
        loops = []
        pending = list(loop_stack)
        if pending:
            # single-level loops: close at the message before the next loop or end
            starts = [r for r in rows if r[0] == "loopstart"]
            for k, (label, count, y0) in enumerate(pending):
                # find rows after this loopstart limited to `count` messages
                si = rows.index(starts[k])
                inner = [r for r in rows[si + 1: si + 1 + count] if r[0] in ("msg", "self")]
                last = inner[-1] if inner else rows[si]
                lo = min(min(idx[r[1]["a"]], idx[r[1]["b"]]) for r in inner) if inner else 0
                hi = max(max(idx[r[1]["a"]], idx[r[1]["b"]]) for r in inner) if inner else 0
                loops.append((label, y0, last[2] + 18, lo, hi))

        height = int(y + 40)
        svg = Svg(width, height, self.title)

        # lifelines + heads
        for i, (k, label, pal) in enumerate(self.parts):
            x, hw = xs[i], head_w[i]
            svg.body.append(
                f'<line x1="{x:.1f}" y1="{top + 12}" x2="{x:.1f}" y2="{height - 26}" '
                f'stroke="{EDGE_L}" stroke-width="1.1" stroke-dasharray="4 5" '
                f'opacity="0.65" class="lifeline"/>'
            )
            svg.rect(x - hw / 2, top - 34, hw, 40, pal, rx=9)
            svg.text(x, top - 8, label, size=12.5, pal=pal, weight="650")

        # loop frames (drawn behind messages)
        frames = []
        for label, y0, y1, lo, hi in loops:
            fx0, fx1 = xs[lo] - 52, xs[hi] + 52
            frames.append(
                f'<rect x="{fx0:.1f}" y="{y0}" width="{fx1 - fx0:.1f}" '
                f'height="{y1 - y0}" rx="10" fill="none" stroke="{LIGHT["purple"][1]}" '
                f'stroke-width="1.2" stroke-dasharray="5 5" opacity="0.75" class="purple-s"/>'
            )
            lw = tw(label, 10.5) + 20
            frames.append(
                f'<rect x="{fx0:.1f}" y="{y0}" width="{lw:.1f}" height="19" rx="9" '
                f'fill="{LIGHT["purple"][0]}" stroke="{LIGHT["purple"][1]}" '
                f'stroke-width="1.2" class="purple-b"/>'
            )
            frames.append(
                f'<text x="{fx0 + lw / 2:.1f}" y="{y0 + 13.5}" font-family="{FONT}" '
                f'font-size="10.5" font-weight="650" fill="{LIGHT["purple"][2]}" '
                f'text-anchor="middle" class="purple-t">{esc(label)}</text>'
            )
        svg.body.extend(frames)

        # messages
        num = 0
        for kind, m, yy in rows:
            if kind == "loopstart":
                continue
            if kind == "note":
                i, j = sorted((idx[m["a"]], idx[m["b"]]))
                x0, x1 = xs[i] - 30, xs[j] + 30
                svg.rect(x0, yy - 4, x1 - x0, 30, "amber", rx=7)
                svg.text((x0 + x1) / 2, yy + 16, m["lines"][0], size=10.8,
                         pal="amber", weight="500")
                continue

            num += 1
            i, j = idx[m["a"]], idx[m["b"]]
            if m["self_call"]:
                x = xs[i]
                nl = len(m["lines"])
                top_y = yy - 12 * nl - 10
                svg.path(
                    f"M{x:.1f},{top_y} h34 a9,9 0 0 1 9,9 v{12 * nl} a9,9 0 0 1 -9,9 h-30",
                    marker="arw-s", sw=1.4,
                )
                for t, ln in enumerate(m["lines"]):
                    svg.text(x + 54, top_y + 4 + t * 13, ln, size=10.8,
                             anchor="start", cls="mute")
                svg.badge(40, top_y + 14, num, r=9)
                continue

            x0, x1 = xs[i], xs[j]
            d = "5 4" if m["dashed"] else None
            sgn = 1 if x1 > x0 else -1
            svg.line(x0 + sgn * 4, yy, x1 - sgn * 5, yy, dash=d, sw=1.5)
            mid = (x0 + x1) / 2
            nl = len(m["lines"])
            for t, ln in enumerate(m["lines"]):
                svg.elabel(mid, yy - 8 - (nl - 1 - t) * 13, ln, size=10.8)
            svg.badge(40, yy - 4, num, r=9)

        return svg


# =====================================================================
# 1. runtime architecture
# =====================================================================
def d1_runtime():
    s = Svg(1180, 600, "Honcho 執行期架構：API server 與 deriver worker")

    # external providers on top, storage at the bottom, our processes in between
    s.node(236, 14, 908, ["LLM / Embedding Providers", "anthropic · openai · gemini"],
           "amber", size=13)
    s.node(36, 208, 176, ["客戶端 / Agent", "Python / TS SDK"], "teal")

    s.panel(236, 96, 300, 252, "API Server", "uv run fastapi dev src/main.py")
    s.node(252, 146, 268, ["Dialectic Agent", "同步、行程內工具迴圈"], "blue")
    s.node(252, 216, 268, ["Routers  /v3/*"], "blue")
    s.node(252, 272, 268, ["enqueue.py", "佇列生產者"], "blue")

    s.panel(568, 96, 576, 252, "Deriver Worker", "uv run python -m src.deriver")
    for i, lines in enumerate([["Deriver", "單次 LLM 呼叫"], ["Summarizer", "兩層摘要"],
                               ["Dreamer", "Deduction/Induction"], ["Reconciler", "向量同步"]]):
        s.node(584 + i * 140, 146, 128, lines, "purple", size=11.5)
    s.node(584, 262, 548, ["QueueManager 輪詢迴圈 — 搶佔 work unit、批次組裝"], "purple")

    s.node(330, 418, 620, ["Postgres + pgvector",
                           "messages · message_embeddings · collections · documents · queue"],
           "green", size=13)
    s.node(36, 418, 268, ["Redis / cashews", "API 與 worker 共用的讀取快取"], "rose", size=12)

    s.line(212, 235, 244, 235)
    s.elabel(228, 226, "HTTP")
    s.vline(386, 214, 203)
    s.vline(352, 255, 268)

    s.path("M400,144 L400,73", marker="arw", start_marker="arw")
    s.elabel(400, 108, "推論 · 工具迴圈")
    for x in (648, 788, 928, 1068):
        s.path(f"M{x},144 L{x},73", marker="arw", start_marker="arw")
    s.elabel(788, 108, "推論 / 結構化輸出")
    s.elabel(1068, 108, "embedding")

    for x in (620, 760, 900, 1040):
        s.vline(x, 260, 201)

    s.elbow([(520, 172), (552, 172), (552, 414)])
    s.elabel(552, 384, "查詢記憶 / 訊息")
    s.vline(352, 327, 414)
    s.elabel(352, 384, "INSERT queue")
    s.vline(660, 414, 301)
    s.elabel(660, 384, "claim work unit")
    s.vline(860, 301, 414)
    s.elabel(860, 384, "標記 processed")
    s.elbow([(1132, 172), (1164, 172), (1164, 392), (952, 392), (952, 414)])
    s.elabel(1052, 386, "寫入 documents / summaries")

    for i, t in enumerate(["實線 = 資料流", "雙箭頭 = 往返呼叫",
                           "queue 是 Postgres 的一張表", "Redis 為讀取快取，兩個行程共用"]):
        s.text(978, 452 + i * 19, t, size=10.5, anchor="start", cls="mute")

    s.text(590, 566, "外部供應商在上、儲存在下、兩個行程在中間 — "
                     "HTTP 請求路徑上永遠不等待 LLM", size=11.5, cls="mute")
    s.save("01-runtime-architecture.svg")


# =====================================================================
# 2. data model
# =====================================================================
def d2_model():
    s = Svg(1060, 626, "Honcho 資料模型與關聯")

    def tbl(x, y, w, name, cols, pal, star=False):
        h = 30 + len(cols) * 15 + 9
        s.rect(x, y, w, h, pal, rx=9, sw=2 if star else 1.4)
        s.body.append(
            f'<path d="M{x},{y + 27} h{w}" stroke="{LIGHT[pal][1]}" stroke-width="1.1" '
            f'opacity="0.5" class="{pal}-s"/>'
        )
        s.text(x + w / 2, y + 19, name, size=12.5, pal=pal, weight="700", mono=True)
        for i, c in enumerate(cols):
            s.text(x + 12, y + 43 + i * 15, c, size=10, pal=pal, anchor="start",
                   mono=True, opacity="0.85")
        return (x, y, w, h)

    tbl(430, 22, 200, "workspaces", ["name (PK)"], "gray")
    tbl(392, 116, 276, "session_peers", ["observe_me / observe_others",
                                         "joined_at, left_at"], "amber")
    tbl(56, 212, 268, "peers", ["name, workspace_name",
                                "internal_metadata", "  └ peer_card  ★"], "teal")
    tbl(736, 212, 268, "sessions", ["name, workspace_name",
                                    "internal_metadata", "  └ summaries  ★"], "teal")
    tbl(56, 352, 300, "collections  ★", ["observer, observed, workspace_name",
                                         "UNIQUE(observer, observed, ws)",
                                         "— 沒有 session_name 欄位"], "blue", star=True)
    tbl(736, 352, 288, "messages", ["seq_in_session",
                                    "content  (GIN 全文索引)",
                                    "peer_name, session_name"], "gray")
    tbl(56, 480, 300, "documents", ["content, level, embedding",
                                    "source_ids, times_derived",
                                    "session_name  (僅溯源)"], "blue")
    tbl(736, 480, 288, "message_embeddings", ["embedding  (HNSW)", "sync_state"], "gray")

    def link(pts, label, lx, ly, anchor="middle", curve=None):
        if curve:
            s.path(curve, marker=None, sw=1.4)
        else:
            s.elbow(pts, marker=None, sw=1.4)
        s.elabel(lx, ly, label, size=10, anchor=anchor)

    link(None, "1 — N", 300, 150, curve="M470,76 C470,110 330,120 190,208")
    link(None, "1 — N", 796, 150, curve="M590,76 C590,110 730,120 870,208")
    link([(392, 150), (324, 150), (324, 208)], "N", 348, 142)
    link([(668, 150), (736, 150), (736, 208)], "N", 712, 142)
    link([(190, 296), (190, 348)], "observer / observed", 200, 324, "start")
    link([(206, 436), (206, 476)], "1 — N", 216, 462, "start")
    link([(870, 296), (870, 348)], "1 — N", 880, 324, "start")
    link([(870, 436), (870, 476)], "1 — N", 880, 462, "start")
    link([(324, 260), (540, 260), (540, 392), (732, 392)], "1 — N（作者）", 540, 252)

    s.text(530, 596, "★ = 記憶所在。collections 的唯一鍵不含 session_name，"
                     "因此記憶天生跨 session；documents.session_name 只用於溯源與刪除",
           size=11.5, cls="mute")
    s.save("02-data-model.svg")


# =====================================================================
# 3. memory layers
# =====================================================================
def d3_layers():
    s = Svg(980, 470, "Honcho 的四層記憶")

    s.node(48, 40, 400, ["① 原始訊息  messages",
                         "粒度最細 · 永久保存",
                         "全文（立即）＋語意（非同步）搜尋"], "gray", size=13.5)
    s.node(540, 40, 396, ["② 會話摘要  summaries",
                          "sessions.internal_metadata",
                          "短摘要每 20 則 · 長摘要每 60 則"], "gray", size=13.5)
    s.node(48, 196, 470, ["③ 結論  documents（conclusions）",
                          "collection(observer, observed) — 不含 session",
                          "explicit / deductive / inductive / contradiction"],
           "blue", size=14, sw=2)
    s.node(48, 336, 470, ["④ Peer Card",
                          "peers.internal_metadata",
                          "最多 40 條穩定身分標記 · 直接注入 system prompt"],
           "amber", size=14, sw=2)

    s.line(283, 118, 283, 192)
    s.elabel(283, 162, "Deriver 單次 LLM 呼叫")
    s.line(452, 79, 536, 79)
    s.elabel(494, 70, "Summarizer")
    s.line(283, 274, 283, 332)
    s.elabel(283, 310, "Dreamer  update_peer_card")

    s.path("M416,194 C416,164 506,164 506,192", marker="arw", sw=1.5)
    s.elabel(461, 158, "Dreamer 跨 session 整併")

    s.rect(556, 196, 380, 198, "panel", rx=11, dash="6 5", sw=1.3)
    s.text(576, 224, "範圍", size=12, pal="panel", anchor="start", weight="700")
    for i, (t, c) in enumerate([
        ("① 訊息　　　session 內（檢索可跨）", "gray"),
        ("② 摘要　　　單一 session ← 唯一綁定的一層", "gray"),
        ("③ 結論　　　跨 session · 跨時間", "blue"),
        ("④ Peer Card　跨 session · 全域", "amber"),
    ]):
        s.text(576, 252 + i * 27, t, size=11.5, pal=c, anchor="start",
               weight="600" if c != "gray" else "400")

    s.save("03-memory-layers.svg")


# =====================================================================
# 4. write path (sequence)
# =====================================================================
def d4_write():
    q = Seq("寫入路徑：訊息如何變成記憶", [
        ("C", "客戶端", "teal"),
        ("API", "API Server", "blue"),
        ("PG", "Postgres（含 queue 表）", "green"),
        ("W", "Deriver Worker", "purple"),
        ("LLM", "LLM", "amber"),
        ("EMB", "Embedding API", "rose"),
    ])
    q.msg("C", "API", ["POST /v3/.../sessions/{id}/messages（最多 100 則）"])
    q.msg("API", "PG", ["crud.create_messages()：寫 messages（配 seq_in_session）",
                        "+ message_embeddings（sync_state='pending'，embedding 為 NULL）"])
    q.msg("API", "API", ["enqueue()：取消該 peer 的 pending dream"], self_call=True)
    q.msg("API", "PG", ["解析 session / peer 設定（observe_me、observe_others）"])
    q.msg("API", "PG", ["INSERT queue（representation，必要時加 summary）"])
    q.msg("API", "C", ["201 立刻回應 — 不等 LLM"], dashed=True)
    q.loop("QueueManager 輪詢", 2)
    q.msg("W", "PG", ["查可用 work unit（累積 token ≥ 門檻，或已逾時）"])
    q.msg("W", "PG", ["INSERT active_queue_sessions（ON CONFLICT DO NOTHING）→ 搶佔"])
    q.msg("W", "PG", ["get_queue_item_batch()：抓訊息 + 交錯脈絡"])
    q.msg("W", "LLM", ["minimal_deriver_prompt — 單次結構化輸出呼叫"])
    q.msg("LLM", "W", ["PromptRepresentation { explicit: [...] }"], dashed=True)
    q.msg("W", "EMB", ["simple_batch_embed(所有結論文字)"])
    q.msg("EMB", "W", ["向量"], dashed=True)
    q.msg("W", "PG", ["每個 observer：get_or_create_collection + create_documents（含去重）"])
    q.msg("W", "PG", ["check_and_schedule_dream()：達門檻就排定 dream"])
    q.msg("W", "PG", ["標記 queue items 為 processed"])
    q.build().save("04-write-path.svg")


# =====================================================================
# 5. semantic dedup
# =====================================================================
def d5_dedup():
    s = Svg(980, 500, "語意去重的判定流程")

    s.node(390, 30, 200, ["新 document"], "gray", size=13)
    s.node(255, 116, 470, ["向量搜尋  max_distance = 0.05",
                           "（≈ cosine 相似度 ≥ 0.95）",
                           "同 level；explicit 另限同 session"], "blue", size=12.5)
    s.node(716, 208, 240, ["NOT_DUPLICATE", "正常寫入"], "green", size=12.5)
    s.node(255, 236, 470, ["token 集合比較",
                           "score = len(tokens) + unique_tokens × 10"], "amber", size=12.5)
    s.node(30, 356, 430, ["REPLACED_EXISTING",
                          "舊的 soft-delete（deleted_at）",
                          "新的繼承 times_derived + 1"], "teal", size=12.5)
    s.node(500, 356, 430, ["REJECTED",
                           "舊的 times_derived += 1",
                           "新的丟棄"], "rose", size=12.5)

    s.line(490, 84, 490, 112)
    s.path("M725,151 C770,151 800,170 812,204")
    s.elabel(790, 168, "沒找到")
    s.line(490, 196, 490, 232)
    s.elabel(490, 220, "找到 existing")
    s.path("M340,304 C340,330 300,330 245,352")
    s.elabel(258, 330, "score_new ≥ score_existing")
    s.path("M640,304 C640,330 690,330 715,352")
    s.elabel(722, 330, "score_new < score_existing")

    s.text(490, 466, "兩種結果都不丟棄資訊：times_derived 記錄「這條結論又被導出一次」",
           size=11.5, cls="mute")
    s.save("05-semantic-dedup.svg")


# =====================================================================
# 6. dream scheduling
# =====================================================================
def d6_dream_sched():
    s = Svg(1000, 700, "Dream 的排程與取消")

    s.node(300, 26, 400, ["每次寫入 documents 後",
                          "check_and_schedule_dream()"], "gray", size=12.5)
    s.diamond(500, 152, 400, 92,
              ["explicit document 數 − 上次做夢時的數量", "≥ DREAM_DOCUMENT_THRESHOLD（50）？"])
    s.diamond(500, 282, 400, 84,
              ["距上次做夢 ≥ MIN_HOURS_BETWEEN_DREAMS（8h）？"])
    s.diamond(500, 396, 400, 76, ["queue 中已有 pending dream？"])
    s.node(300, 468, 400, ["schedule_dream()",
                           "asyncio 計時器，延遲 IDLE_TIMEOUT_MINUTES（60）"],
           "purple", size=12.5)
    s.diamond(500, 592, 330, 70, ["計時期間有新訊息進來？"])

    s.node(806, 128, 168, ["不排程"], "gray", size=12.5)
    s.node(742, 556, 240, ["cancel_dreams_for_observed()",
                           "取消，重新等待閒置"], "rose", size=11.5)
    s.node(30, 556, 236, ["enqueue_dream()", "→ 寫入 queue"], "green", size=12)
    s.node(30, 640, 236, ["worker → process_dream()"], "green", size=12)

    s.line(500, 78, 500, 108)
    s.line(500, 198, 500, 242)
    s.elabel(510, 226, "是", anchor="start")
    s.line(500, 324, 500, 360)
    s.elabel(510, 346, "是", anchor="start")
    s.line(500, 434, 500, 464)
    s.elabel(510, 456, "否", anchor="start")
    s.line(500, 546, 500, 559)

    for y in (152, 282, 396):
        s.path(f"M700,{y} C760,{y} 780,{y} 830,168" if y != 152 else "M700,152 C760,152 800,152 830,168")
    s.elabel(760, 144, "否")
    s.elabel(772, 276, "否")
    s.elabel(772, 390, "是")

    s.path("M665,592 C700,592 710,580 738,576")
    s.elabel(700, 584, "是", anchor="start")
    s.elbow([(335, 592), (296, 592), (296, 556), (270, 556)])
    s.elabel(304, 546, "否，計時到", anchor="start")
    s.line(148, 606, 148, 636)

    s.text(500, 682, "三個條件必須同時滿足；任何新訊息都會取消計時器 — "
                     "見 §17.2 的排程互卡問題", size=11.5, cls="mute")
    s.save("06-dream-scheduling.svg")


# =====================================================================
# 7. dream phases
# =====================================================================
def d7_dream_phases():
    s = Svg(1130, 250, "Dream 的執行階段")
    items = [
        (["0 · Surprisal 取樣", "可選，預設關閉", "產生 exploration hints"], "gray"),
        (["1 · DeductionSpecialist", "explicit → deductive", "唯一可寫 peer card"], "purple"),
        (["2 · InductionSpecialist", "explicit + deductive → inductive", "無 update_peer_card"], "purple"),
        (["3 · Guard pair 原子更新", "last_dream_at +", "last_dream_document_count"], "green"),
    ]
    x = 42
    for i, (lines, pal) in enumerate(items):
        w = 244
        s.node(x, 62, w, lines, pal, size=12.5, lead=17)
        if i < 3:
            s.line(x + w + 6, 116, x + w + 22, 116)
        x += w + 28

    s.text(565, 30, "orchestrator.run_dream() — 兩個 specialist 依序執行",
           size=12.5, weight="600")
    s.text(565, 210, "階段 2 排在階段 1 之後，才看得見剛產生的 deductive 結論；"
                     "階段 3 失敗不推進 baseline，下次重試", size=11.5, cls="mute")
    s.save("07-dream-phases.svg")


# =====================================================================
# 8. read path (sequence)
# =====================================================================
def d8_read():
    q = Seq("讀取路徑：Dialectic 如何回想", [
        ("C", "客戶端", "teal"),
        ("R", "POST /peers/{id}/chat", "blue"),
        ("A", "DialecticAgent", "purple"),
        ("EMB", "Embedding", "rose"),
        ("PG", "Postgres", "green"),
        ("LLM", "LLM", "amber"),
    ])
    q.msg("C", "R", ["{ query, target?, session_id?, stream?, reasoning_level }"])
    q.msg("R", "R", ["授權檢查（peer-scoped key 必須是該 session 成員）"], self_call=True)
    q.msg("R", "PG", ["preflight（read_only）：驗證 peer、取 configuration"])
    q.msg("R", "PG", ["get_peer_card(observer→observer) 與 (observer→observed)"])
    q.note("R", "PG", "DB session 在此關閉 — 之後全程不持有連線")
    q.msg("R", "A", ["建立 DialecticAgent（peer card 注入 system prompt）"])
    q.msg("A", "PG", ["_initialize_session_history()：撈最近訊息",
                      "（≤ SESSION_HISTORY_MAX_TOKENS = 4096）"])
    q.msg("A", "A", ["附加 <session_history> 區塊到 system prompt"], self_call=True)
    q.msg("A", "EMB", ["embed(query) — 算一次，兩次搜尋共用"])
    q.msg("A", "PG", ["search_memory(levels=[\"explicit\"], limit=25)"])
    q.msg("A", "PG", ["search_memory(levels=[\"deductive\",\"inductive\",\"contradiction\"], limit=25)"])
    q.msg("A", "A", ["組成 user message：Query + 已預取的相關結論"], self_call=True)
    q.loop("最多 MAX_TOOL_ITERATIONS 次（依 reasoning level）", 4)
    q.msg("A", "LLM", ["messages + DIALECTIC_TOOLS"])
    q.msg("LLM", "A", ["tool_use"], dashed=True)
    q.msg("A", "PG", ["執行工具（每個工具管自己的短命 DB session）"])
    q.msg("A", "LLM", ["tool_result"])
    q.msg("LLM", "A", ["最終回答（可 SSE 串流）"], dashed=True)
    q.msg("A", "C", ["回應 + 發出 DialecticCompletedEvent"], dashed=True)
    q.build().save("08-read-path.svg")


# =====================================================================
# 9. cross-session memory
# =====================================================================
def d9_cross():
    s = Svg(1180, 620, "跨 session 記憶：為什麼新 session 讀得到舊記憶")

    for name, when, y in [("Session #1", "一月", 36), ("Session #2", "三月", 208),
                          ("Session #3", "今天", 380)]:
        s.panel(36, y, 300, 144, f"{name}（{when}）")
        s.node(54, y + 62, 128, ["messages"], "gray", size=11.5)
        s.node(196, y + 62, 124, ["summaries", "僅屬於本 session"], "gray", size=11)

    s.node(470, 150, 380, ["collection(workspace, alice, alice)",
                           "documents：explicit / deductive / inductive",
                           "唯一鍵不含 session_name"], "blue", size=13.5, sw=2.2)
    s.node(470, 320, 380, ["peers.internal_metadata['peer_card']",
                           "最多 40 條穩定身分標記"], "amber", size=13.5, sw=2.2)
    s.node(900, 420, 250, ["Session #3 的", "Dialectic 回答"], "teal", size=12.5)

    for y0, y1 in ((116, 196), (288, 214), (460, 232)):
        s.path(f"M340,{y0} C400,{y0} 410,{y1} 466,{y1}")
    s.elabel(404, 108, "Deriver")
    s.elabel(404, 262, "Deriver")
    s.elabel(404, 400, "Deriver")

    s.path("M600,148 C600,112 720,112 720,146", marker="arw", sw=1.7)
    s.elabel(660, 104, "Dreamer 跨 session 整併")

    s.vline(660, 240, 316)
    s.elabel(660, 286, "Dreamer  update_peer_card")

    s.elbow([(850, 190), (1030, 190), (1030, 416)])
    s.elabel(968, 182, "search_memory — 不帶 session filter")
    s.elbow([(850, 352), (952, 352), (952, 416)])
    s.elabel(901, 344, "注入 system prompt")

    s.rect(36, 546, 1108, 48, "panel", rx=10, dash="6 5", sw=1.3)
    s.text(590, 575,
           "只有「摘要」綁定單一 session。結論與 peer card 掛在 (observer, observed) 之下，"
           "所以跨 session 是預設行為 — 需要額外設定的反而是「限制在單一 session」",
           size=11.5, cls="mute")
    s.save("09-cross-session-memory.svg")


if __name__ == "__main__":
    print("generating diagrams:")
    d1_runtime(); d2_model(); d3_layers(); d4_write(); d5_dedup()
    d6_dream_sched(); d7_dream_phases(); d8_read(); d9_cross()
    print("done")
