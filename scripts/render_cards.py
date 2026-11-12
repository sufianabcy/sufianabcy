#!/usr/bin/env python3
"""Render the animated SVG cards used by the profile README.

  python scripts/render_cards.py            fetch fresh contributions, then render every card
  OFFLINE=1 python scripts/render_cards.py  render from data/contributions.json without fetching
  STATIC=1 python scripts/render_cards.py   render every card at its final frame (for screenshots)

Layout lives in README.md; every visual and every animation lives inside the SVGs.
An SVG shown through <img> runs its own <style> (CSS keyframes) and SMIL, but cannot
load web fonts, so the fonts are subset and embedded as base64 WOFF.

Palette: Midnight Blue #1E223D and Exotic Orange #F54F1B.
Type: Instrument Serif Italic for display, Inter for everything else.
"""
import base64
import datetime as dt
import html
import io
import json
import os
import random
import re
import struct
import urllib.request
import zlib
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
CARDS, FONTS, DATA = ROOT / "cards", ROOT / "fonts", ROOT / "data" / "contributions.json"
LOGIN = "sufianabcy"  # the contribution graph and activity card only ever read this account
STATIC = os.environ.get("STATIC") == "1"
TODAY = dt.date.fromisoformat(os.environ["TODAY"]) if "TODAY" in os.environ else dt.date.today()

# Midnight Blue / Exotic Orange
MID, MID2, MID3 = "#1E223D", "#2C3154", "#3B4170"  # surface, empty mark, quiet mark
ORANGE, ORANGE_TXT, PEACH = "#F54F1B", "#FF6838", "#FAB18B"  # brand, small orange text (5.4:1), light tint
TEXT, SOFT, DIM = "#F6EEE6", "#C9C6D6", "#9195B3"  # text on midnight: 13.5, 9.3, 5.3 : 1
ON_OR, ON_OR_DEEP = "#1E223D", "#14172B"  # text on orange: display, small text (5.1:1)
HEAT = [MID2, "#933F28", "#C34825", ORANGE, PEACH]  # empty + one-hue ramp, checked against the midnight surface
SPRING, OUT = "cubic-bezier(.34,1.42,.64,1)", "cubic-bezier(.2,.8,.2,1)"


# ---------------------------------------------------------------- data

def fetch_contributions():
    days = {}
    for year in sorted({TODAY.year - 1, TODAY.year}):
        url = f"https://github.com/users/{LOGIN}/contributions?from={year}-01-01&to={year}-12-31"
        req = urllib.request.Request(url, headers={"User-Agent": "profile-cards-renderer"})
        page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
        tips = dict(re.findall(r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>([^<]*)</tool-tip>', page))
        for tag in re.findall(r"<td\b[^>]*\bdata-date=[^>]*>", page):
            date = re.search(r'data-date="([^"]+)"', tag).group(1)
            cid = re.search(r'\bid="([^"]+)"', tag)
            m = re.match(r"\s*(\d+|No) contribution", tips.get(cid.group(1), "") if cid else "")
            if m:
                days[date] = 0 if m.group(1) == "No" else int(m.group(1))
    if not days:
        raise RuntimeError("no contribution days parsed; the calendar markup may have changed")
    return days


def load_contributions():
    saved = json.loads(DATA.read_text()) if DATA.exists() else {}
    if saved.get("login") != LOGIN:  # never mix in another account's days
        saved = {"login": LOGIN, "days": {}}
    if os.environ.get("OFFLINE") == "1":
        return saved["days"]
    try:
        fresh = fetch_contributions()
    except Exception as exc:  # keep the last good data rather than failing the daily run
        print(f"fetch failed, using saved data: {exc}")
        return saved["days"]
    merged = dict(sorted({**saved["days"], **fresh}.items()))
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps({"login": LOGIN, "days": merged}, indent=0) + "\n")
    return merged


def stats(days):
    start = TODAY - dt.timedelta(days=364)
    window = [(start + dt.timedelta(days=i)) for i in range(365)]
    counts = [days.get(d.isoformat(), 0) for d in window]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    busiest = max(range(365), key=lambda i: (counts[i], i))
    months = []
    y, m = TODAY.year, TODAY.month
    for _ in range(12):
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months.reverse()
    per_month = [(dt.date(y, m, 1), sum(v for k, v in days.items() if k.startswith(f"{y}-{m:02d}-"))) for y, m in months]
    return {
        "total": sum(counts),
        "active": sum(1 for c in counts if c),
        "longest": longest,
        "busiest": (counts[busiest], window[busiest]),
        "months": per_month,
    }


# ---------------------------------------------------------------- fonts and text

FONT_SPECS = {
    "serif": ("instrument-regular.ttf", "IS", "normal", 400),
    "serif-i": ("instrument-italic.ttf", "IS", "italic", 400),
    "sans-l": ("inter-300.ttf", "IN", "normal", 300),
    "sans": ("inter-400.ttf", "IN", "normal", 400),
    "sans-b": ("inter-600.ttf", "IN", "normal", 600),
}
GLYPHS = "".join(chr(c) for c in range(32, 127)) + "·—–→↗•…’“”"
_face_cache, _metric_cache = {}, {}


def font_face(key):
    if key not in _face_cache:
        file, family, style, weight = FONT_SPECS[key]
        opts = subset.Options()
        opts.flavor = "woff"
        opts.layout_features = ["kern", "liga"]
        opts.hinting = False
        opts.name_IDs = []
        font = TTFont(FONTS / file)
        sub = subset.Subsetter(opts)
        sub.populate(text=GLYPHS)
        sub.subset(font)
        buf = io.BytesIO()
        font.flavor = "woff"
        font.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode()
        _face_cache[key] = (f"@font-face{{font-family:{family};font-style:{style};font-weight:{weight};"
                            f"src:url(data:font/woff;base64,{b64}) format('woff')}}")
    return _face_cache[key]


def measure(key, text, size, spacing=0.0):
    """Advance width of text in px, so layout can wrap and size to fit instead of guessing."""
    if key not in _metric_cache:
        font = TTFont(FONTS / FONT_SPECS[key][0])
        _metric_cache[key] = (font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm)
    cmap, hmtx, upm = _metric_cache[key]
    units = sum(hmtx[cmap.get(ord(ch), ".notdef")][0] for ch in text)
    return units / upm * size + spacing * max(0, len(text) - 1)


def wrap(text, key, size, width, lines=99):
    out, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(key, trial, size) > width:
            out.append(cur)
            cur = word
        else:
            cur = trial
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(".,;:") + "…"
    return out


BASE_CSS = f"""
.is{{font-family:IS,Georgia,serif}} .it{{font-family:IS,Georgia,serif;font-style:italic}}
.sn{{font-family:IN,-apple-system,'Segoe UI',Helvetica,sans-serif}} .l{{font-weight:300}} .b{{font-weight:600}}
.cap{{font-family:IN,-apple-system,'Segoe UI',Helvetica,sans-serif;font-weight:600;font-size:12px;letter-spacing:2.4px}}
.rise{{animation:rise .9s {OUT} both}}
.fade{{animation:fade .8s {OUT} both}}
.pulse{{animation:pulse 2.4s ease-out infinite;transform-box:fill-box;transform-origin:center}}
.nudge{{animation:nudge 2.8s ease-in-out infinite}}
@keyframes rise{{from{{opacity:0;transform:translateY(26px)}}}}
@keyframes fade{{from{{opacity:0}}}}
@keyframes pulse{{0%{{opacity:.9;transform:scale(1)}}80%,100%{{opacity:0;transform:scale(3.2)}}}}
@keyframes nudge{{0%,60%,100%{{transform:none}}75%{{transform:translate(4px,-4px)}}}}
"""


def esc(s):
    return html.escape(str(s), quote=True)


def delay(s):
    return f'style="animation-delay:{s:.3f}s"'


def display(x, y, text, size, fill, cls="", extra="", anchor=None):
    """Instrument Serif Italic with a hairline stroke in its own colour, for the weight of the reference type."""
    a = f' text-anchor="{anchor}"' if anchor else ""
    return (f'<text x="{x}" y="{y}" class="it {cls}" font-size="{size}" fill="{fill}" stroke="{fill}" '
            f'stroke-width="{size * .016:.2f}" stroke-linejoin="round"{a} {extra}>{esc(text)}</text>')


def chip(x, y, label, tone="mid", size=12.5, h=28):
    """A hairline box like the reference's HEX CODE label. Returns (svg, width)."""
    w = measure("sans", label, size) + 28
    stroke, fill, ink = (ORANGE, MID, SOFT) if tone == "mid" else (ON_OR, ORANGE, ON_OR_DEEP)
    return (f'<rect x="{x}" y="{y}" width="{w:.1f}" height="{h}" rx="8" fill="{fill}" fill-opacity=".001" stroke="{stroke}" stroke-opacity=".5"/>'
            f'<text x="{x + w / 2:.1f}" y="{y + h / 2 + size * .36:.1f}" class="sn" font-size="{size}" fill="{ink}" text-anchor="middle">{esc(label)}</text>'), w


def svg(w, h, body, title, fonts, css="", defs=""):
    style = "".join(font_face(f) for f in fonts) + BASE_CSS + css
    if STATIC:
        style += "*{animation:none!important}"
        body = re.sub(r"<(animate|animateTransform|set)\b[^>]*/>", "", body)
        body = re.sub(r"<(animate|animateTransform|set)\b[^>]*>.*?</\1>", "", body, flags=re.S)
    else:
        style += "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{esc(title)}"><title>{esc(title)}</title>'
            f"<style>{style}</style><defs>{defs}</defs>{body}</svg>")


# tone: solid colour, a touch lighter at the top, a touch deeper at the bottom, hairline edge, grain strength
TONES = {
    "mid": (MID, "#22274A", "#1B1F38", "#fff", .08, 1),
    "orange": (ORANGE, "#F65B29", "#EC4A17", MID, .16, .9),
}
GRAIN_TILE = 96


def grain_png(size=GRAIN_TILE, seed=7):
    """A small tile of light and dark specks, drawn once and repeated.
    A live SVG noise filter would be recomputed on every animation frame and stall the animations."""
    rnd = random.Random(seed)  # fixed seed: the daily re-render does not churn the files
    raw = bytearray()
    for _ in range(size):
        raw.append(0)  # PNG row filter: none
        for _ in range(size):
            r = rnd.random()
            if r < .33:
                raw += bytes((255, rnd.choice((8, 13, 18))))
            elif r < .66:
                raw += bytes((0, rnd.choice((12, 18, 26))))
            else:
                raw += bytes((0, 0))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 4, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


GRAIN = grain_png()


def frame(w, h, tone="mid"):
    """Card background: one solid colour with a soft fall of light and fine grain, like printed card stock.
    The tone never mixes in a second hue, so it stays as clean as a paint swatch."""
    base, top, bottom, edge, edge_op, grain = TONES[tone]
    defs = (f'<linearGradient id="tone" x1="0" y1="0" x2=".2" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset=".5" stop-color="{base}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>'
            f'<pattern id="grain" width="{GRAIN_TILE}" height="{GRAIN_TILE}" patternUnits="userSpaceOnUse">'
            f'<image width="{GRAIN_TILE}" height="{GRAIN_TILE}" href="data:image/png;base64,{GRAIN}"/></pattern>'
            f'<clipPath id="card"><rect width="{w}" height="{h}" rx="20"/></clipPath>')
    bg_svg = (f'<g clip-path="url(#card)"><rect width="{w}" height="{h}" fill="url(#tone)"/>'
              f'<rect width="{w}" height="{h}" fill="url(#grain)" opacity="{grain}"/></g>')
    border = (f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="19.5" fill="none" '
              f'stroke="{edge}" stroke-opacity="{edge_op}"/>')
    return defs, bg_svg, border


def count_up(value, x, y, cls, start, frames=18, step=0.05, suffix=""):
    """CSS cannot change text, so stack one <text> per frame and flash each in turn."""
    target = int(value)
    seen, vals = set(), []
    for i in range(frames):
        t = i / frames
        v = int(round(target * (1 - (1 - t) ** 3)))
        if v < target and v not in seen:
            seen.add(v)
            vals.append(v)
    out = []
    for i, v in enumerate(vals):
        out.append(f'<text x="{x}" y="{y}" class="{cls} cf" style="animation-delay:{start + i * step:.3f}s">{v}{suffix}</text>')
    done = start + len(vals) * step
    out.append(f'<text x="{x}" y="{y}" class="{cls} cfin" style="animation-duration:{done:.3f}s">{target}{suffix}</text>')
    return "".join(out)


COUNT_CSS = f"""
.cf{{opacity:0;animation:flash .05s step-end}}
.cfin{{animation:hold linear}}
@keyframes flash{{0%,100%{{opacity:1}}}}
@keyframes hold{{0%,100%{{opacity:0}}}}
"""


# ---------------------------------------------------------------- cards

def hero():
    w, h = 1200, 470
    gdefs, bg, border = frame(w, h)
    defs = gdefs + f"""
<linearGradient id="shine" gradientUnits="userSpaceOnUse" x1="0" y1="120" x2="620" y2="340" gradientTransform="translate(-900 0)">
  <stop offset="0" stop-color="{ORANGE}"/><stop offset=".40" stop-color="{ORANGE}"/>
  <stop offset=".5" stop-color="{PEACH}"/><stop offset=".60" stop-color="{ORANGE}"/><stop offset="1" stop-color="{ORANGE}"/>
  <animateTransform attributeName="gradientTransform" type="translate" values="-900 0;900 0;900 0" keyTimes="0;.32;1" dur="7s" begin="2.2s" repeatCount="indefinite"/>
</linearGradient>"""
    first, last = "Abu Sufian", "Choudhury"
    name_size = min(134, int(560 / max(measure("serif-i", first, 1), measure("serif-i", last, 1))))
    words = ["secure backends.", "full-stack apps.", "security tools."]
    word_size = min(54, int(470 / max(measure("serif-i", wd, 1) for wd in words)))
    word_svg = "".join(display(664, 252, wd, word_size, ORANGE, f"word w{i}", f'style="animation-delay:{1.3 + i * 3:.1f}s"')
                       for i, wd in enumerate(words))
    para = wrap("From a 50-endpoint hospital platform to a threat-detection engine mapped to MITRE ATT&CK, "
                "I build software that holds up.", "sans", 18, 466, 3)
    para_svg = "".join(f'<text x="666" y="{306 + i * 27}" class="sn rise" font-size="18" fill="{SOFT}" {delay(1.1 + i * .08)}>{esc(t)}</text>'
                       for i, t in enumerate(para))
    label, value = "STATUS:", "Open to internships"
    pill_w = 52 + measure("sans-l", label + " ", 15) + measure("sans-b", value, 15) + 20
    body = f"""{bg}
<g class="rise" {delay(.1)}><rect x="64" y="84" width="28" height="2" rx="1" fill="{ORANGE}"/>
<text x="104" y="90" class="cap" fill="{DIM}">B.TECH CSE · CYBERSECURITY</text></g>
{display(56, 214, first, name_size, "url(#shine)", "rise", delay(.25))}
{display(56, 344, last, name_size, "url(#shine)", "rise", delay(.42))}
<text x="66" y="404" class="sn rise" font-size="15" fill="{DIM}" {delay(.6)}>KL University · Class of 2028 · CGPA 9.0</text>
{display(664, 190, "Builds", 54, TEXT, "rise", delay(.8))}
{word_svg}
{para_svg}
<g class="rise" {delay(1.45)}>
  <rect x="664" y="384" width="{pill_w:.0f}" height="40" rx="8" fill="{ORANGE}" fill-opacity=".06" stroke="{ORANGE}" stroke-opacity=".6"/>
  <circle cx="688" cy="404" r="4.5" fill="{ORANGE}" class="pulse"/><circle cx="688" cy="404" r="4.5" fill="{ORANGE}"/>
  <text x="704" y="409.5" class="sn l" font-size="15" fill="{ORANGE_TXT}">{label} <tspan class="b">{esc(value)}</tspan></text>
</g>
{border}"""
    css = f"""
.word{{animation:cycle 9s {OUT} infinite backwards}} .w1,.w2{{opacity:0}}
@keyframes cycle{{0%{{opacity:0;transform:translateY(22px)}}5%,30%{{opacity:1;transform:none}}35%,100%{{opacity:0;transform:translateY(-16px)}}}}
"""
    return svg(w, h, body, "Abu Sufian Choudhury. Builds secure backends, full-stack apps and security tools. Open to internships.",
               ["serif-i", "sans-l", "sans", "sans-b"], css, defs)


def graph(s, days):
    w, h = 1200, 300
    gdefs, bg, border = frame(w, h)
    step, cell = 20, 16
    last_sunday = TODAY - dt.timedelta(days=(TODAY.weekday() + 1) % 7)
    first = last_sunday - dt.timedelta(weeks=52)
    x0, y0 = 72, 118

    def level(c):
        return 0 if c == 0 else 1 if c <= 2 else 2 if c <= 5 else 3 if c <= 9 else 4

    cells, labels, last_label_col = [], [], -9
    for col in range(53):
        week = first + dt.timedelta(weeks=col)
        if week.day <= 7 and col - last_label_col >= 3:
            labels.append(f'<text x="{x0 + col * step}" y="104" class="sn fade" font-size="12" fill="{DIM}" {delay(.2)}>{week.strftime("%b")}</text>')
            last_label_col = col
        for row in range(7):
            d = week + dt.timedelta(days=row)
            if d > TODAY:
                continue
            c = days.get(d.isoformat(), 0)
            tip = f"{c} contribution{'s' if c != 1 else ''} on {d:%b} {d.day}"
            cells.append(f'<rect x="{x0 + col * step}" y="{y0 + row * step}" width="{cell}" height="{cell}" rx="4" '
                         f'fill="{HEAT[level(c)]}" class="pop c{col}"><title>{tip}</title></rect>')
    legend = "".join(f'<rect x="{1010 + i * 20}" y="268" width="14" height="14" rx="3.5" fill="{c}"/>' for i, c in enumerate(HEAT))
    body = f"""{bg}
{display(64, 72, "Contributions", 40, ORANGE, "rise")}
<text x="1136" y="70" class="sn b rise" font-size="15" fill="{SOFT}" text-anchor="end" {delay(.1)}>{s['total']} in the last year</text>
{''.join(labels)}{''.join(cells)}
<g class="fade" {delay(1.6)}><text x="64" y="280" class="sn" font-size="13" fill="{DIM}">github.com/{LOGIN}</text>
<text x="1000" y="280" class="sn" font-size="12" fill="{DIM}" text-anchor="end">Less</text>{legend}
<text x="1112" y="280" class="sn" font-size="12" fill="{DIM}">More</text></g>
{border}"""
    css = (f".pop{{animation:pop .55s {SPRING} both;transform-box:fill-box;transform-origin:center}}"
           f"@keyframes pop{{from{{opacity:0;transform:scale(.2)}}}}"
           + "".join(f".c{i}{{animation-delay:{.25 + i * .024:.3f}s}}" for i in range(53)))
    return svg(w, h, body, f"{s['total']} GitHub contributions by {LOGIN} in the last year", ["serif-i", "sans", "sans-b"], css, gdefs)


def activity(s):
    w, h = 1200, 380
    gdefs, bg, border = frame(w, h)
    defs = gdefs + (f'<linearGradient id="best" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="{ORANGE}"/>'
                    f'<stop offset="1" stop-color="{PEACH}"/></linearGradient>')
    months = s["months"]
    peak = max(v for _, v in months) or 1
    best = max(range(12), key=lambda i: (months[i][1], i))
    left, right, base, tall = 64, 740, 312, 176
    slot = (right - left) / 12
    bars = []
    for i, (d, v) in enumerate(months):
        bw = 30
        x = left + i * slot + (slot - bw) / 2
        bh = max(4, v / peak * tall)
        fill = "url(#best)" if i == best and v else (MID3 if v else MID2)
        bars.append(f'<rect x="{x:.1f}" y="{base - bh:.1f}" width="{bw}" height="{bh:.1f}" rx="6" fill="{fill}" class="grow" '
                    f'{delay(.3 + i * .06)}><title>{v} in {d:%B %Y}</title></rect>')
        bars.append(f'<text x="{x + bw / 2:.1f}" y="{base + 26}" class="sn fade" font-size="12" fill="{SOFT if i == best else DIM}" '
                    f'text-anchor="middle" {delay(.3 + i * .06)}>{d:%b}</text>')
        if i == best and v:
            bars.append(display(f"{x + bw / 2:.1f}", f"{base - bh - 12:.1f}", v, 30, TEXT, "popv", anchor="middle"))
    count, day = s["busiest"]
    tiles = [
        ("CONTRIBUTIONS", s["total"], "in the last 12 months"),
        ("ACTIVE DAYS", s["active"], "days with commits"),
        ("LONGEST STREAK", s["longest"], "days in a row"),
        ("BUSIEST DAY", count, f"on {day:%b} {day.day}, {day.year}"),
    ]
    st = []
    for i, (label, val, sub) in enumerate(tiles):
        x, y = 820 + (i % 2) * 168, 130 + (i // 2) * 116
        st.append(f'<g class="rise" {delay(.9 + i * .1)}><rect x="{x}" y="{y - 9}" width="8" height="8" rx="2" fill="{ORANGE}"/>'
                  f'<text x="{x + 16}" y="{y}" class="cap" fill="{DIM}" style="font-size:11px;letter-spacing:2px">{label}</text>'
                  f'{display(x - 2, y + 50, val, 50, TEXT)}'
                  f'<text x="{x}" y="{y + 74}" class="sn" font-size="13" fill="{DIM}">{esc(sub)}</text></g>')
    body = f"""{bg}
{display(64, 72, "Monthly activity", 40, ORANGE, "rise")}
<g class="rise" {delay(.1)}><circle cx="1018" cy="65" r="4" fill="{ORANGE}" class="pulse"/><circle cx="1018" cy="65" r="4" fill="{ORANGE}"/>
<text x="1136" y="70" class="sn b" font-size="15" fill="{SOFT}" text-anchor="end">Updated daily</text></g>
<line x1="{left}" x2="{right}" y1="{base + .5}" y2="{base + .5}" stroke="#fff" stroke-opacity=".1"/>
{''.join(bars)}
<line x1="782" x2="782" y1="112" y2="{base + 26}" stroke="#fff" stroke-opacity=".08" class="fade" {delay(.8)}/>
{''.join(st)}
{border}"""
    css = (f".grow{{animation:grow .8s {SPRING} both;transform-box:fill-box;transform-origin:bottom}}"
           f"@keyframes grow{{from{{transform:scaleY(0)}}}}"
           f".popv{{animation:popv .6s {SPRING} both;animation-delay:{.3 + 12 * .06 + .5:.2f}s;transform-box:fill-box;transform-origin:bottom}}"
           f"@keyframes popv{{from{{opacity:0;transform:translateY(10px) scale(.6)}}}}")
    return svg(w, h, body, f"Contributions per month. {s['total']} in the last year, best month {months[best][0]:%B} with {months[best][1]}.",
               ["serif-i", "sans", "sans-b"], css, defs)


def tile(num, label, value, suffix, caption, footer, footer_dim, start, tone="mid"):
    w, h = 400, 320
    mid = tone == "mid"
    gdefs, bg, border = frame(w, h, tone)
    big, accent, soft, dim, rule = (TEXT, ORANGE_TXT, SOFT, DIM, "#fff") if mid else (ON_OR, ON_OR, ON_OR_DEEP, ON_OR_DEEP, ON_OR)
    sweep = ("#fff", .07) if mid else ("#fff", .16)
    defs = gdefs + (f'<linearGradient id="sweep" x1="0" x2="1"><stop offset="0" stop-color="{sweep[0]}" stop-opacity="0"/>'
                    f'<stop offset=".5" stop-color="{sweep[0]}" stop-opacity="{sweep[1]}"/><stop offset="1" stop-color="{sweep[0]}" stop-opacity="0"/></linearGradient>')
    cap = "".join(f'<text x="36" y="{214 + i * 25}" class="sn" font-size="17" fill="{soft}">{esc(t)}</text>' for i, t in enumerate(caption))
    body = f"""<g class="swing" style="animation-delay:{start:.2f}s">{bg}
<g clip-path="url(#card)"><circle cx="400" cy="0" r="46" fill="none" stroke="{accent}" stroke-width="1.5" class="ring" style="animation-delay:{start + 1.2:.2f}s"/>
<rect x="0" y="-40" width="150" height="400" fill="url(#sweep)" class="sweep" style="animation-delay:{start + 2:.2f}s"/></g>
<text x="36" y="58" class="cap" fill="{accent}">{num} — {esc(label)}</text>
{count_up(value, 30, 178, 'it big', start + .55, suffix=suffix)}
{cap}
<line x1="36" x2="364" y1="268.5" y2="268.5" stroke="{rule}" stroke-opacity=".14"/>
<text x="36" y="297" class="sn b" font-size="14" fill="{big}">{esc(footer)}<tspan class="sn" font-weight="400" fill="{dim}">{esc(footer_dim)}</tspan></text>
<text x="364" y="298" class="sn b nudge" font-size="18" fill="{accent}" text-anchor="end">↗</text>
{border}</g>"""
    css = COUNT_CSS + f"""
.big{{font-size:124px;fill:{big};stroke:{big};stroke-width:2px;stroke-linejoin:round}}
.swing{{animation:swing 1.1s {SPRING} both;transform-box:view-box;transform-origin:50% 100%}}
@keyframes swing{{from{{opacity:0;transform:translateY(70px) rotate(-6deg)}}55%{{opacity:1}}}}
.ring{{animation:ring 3.6s ease-out infinite both;transform-box:fill-box;transform-origin:center;opacity:0}}
@keyframes ring{{0%{{opacity:.7;transform:scale(1)}}70%,100%{{opacity:0;transform:scale(5)}}}}
.sweep{{transform:translateX(-200px) skewX(-18deg);animation:sweep 6s ease-in-out infinite both}}
@keyframes sweep{{0%{{transform:translateX(-200px) skewX(-18deg)}}28%,100%{{transform:translateX(560px) skewX(-18deg)}}}}
"""
    return svg(w, h, body, f"{label.title()}: {value}{suffix} {' '.join(caption)}", ["serif-i", "sans", "sans-b"], css, defs)


def repo_card(name, kind, desc, chips, start):
    w, h = 600, 224
    gdefs, bg, border = frame(w, h)
    lines = "".join(f'<text x="36" y="{134 + i * 23}" class="sn" font-size="15" fill="{SOFT}">{esc(t)}</text>'
                    for i, t in enumerate(wrap(desc, "sans", 15, 528, 2)))
    x, chip_svg = 36, []
    for i, c in enumerate(chips):
        part, cw = chip(x, 175, c)
        if x + cw > 564:
            break
        chip_svg.append(f'<g class="rise" {delay(start + .45 + i * .07)}>{part}</g>')
        x += cw + 8
    body = f"""<g class="rise" {delay(start)}>{bg}
<circle cx="41" cy="45" r="5" fill="{ORANGE}"/><text x="56" y="50" class="cap" style="font-size:11px" fill="{DIM}">{esc(kind)}</text>
<text x="564" y="52" class="sn b nudge" font-size="20" fill="{ORANGE_TXT}" text-anchor="end">↗</text>
{display(34, 100, name, 38, TEXT)}
{lines}{border}</g>{''.join(chip_svg)}"""
    return svg(w, h, body, f"{name}: {desc}", ["serif-i", "sans", "sans-b"], "", gdefs)


def highlights():
    w, h = 1200, 420
    gdefs, bg, border = frame(w, h, "orange")
    left = [
        f'<text x="64" y="132" class="cap" fill="{ON_OR_DEEP}" style="font-size:11px;letter-spacing:2px">INTERNSHIP · DEC 2025 – FEB 2026 · REMOTE</text>',
        display(62, 178, "Engineer Intern, Espaze.in", 36, ON_OR),
    ]
    desc = wrap("Built an image compression service on Google Cloud that shrinks media before it reaches Cloud Storage, "
                "and tested and fixed backend issues in Atlas, the SKU and inventory system.", "sans", 16, 490)
    left += [f'<text x="64" y="{214 + i * 25}" class="sn" font-size="16" fill="{ON_OR_DEEP}">{esc(t)}</text>' for i, t in enumerate(desc)]
    lead_y = 214 + len(desc) * 25 + 34
    left += [
        f'<text x="64" y="{lead_y}" class="cap" fill="{ON_OR_DEEP}" style="font-size:11px;letter-spacing:2px">LEADERSHIP AT KL UNIVERSITY</text>',
        f'<text x="64" y="{lead_y + 28}" class="sn b" font-size="16" fill="{ON_OR_DEEP}">Secretary, IIE Council · Chief, Startup Hub</text>',
        f'<text x="64" y="{lead_y + 52}" class="sn" font-size="16" fill="{ON_OR_DEEP}">E-Cell outreach: 10+ partner E-Cells, Riskopoly with IIT Madras</text>',
    ]
    rows = [
        ("AIR 3", "Entrepreneurship Development Drive", "2026, as team lead"),
        ("1st", "Enigma, IIT Madras", "2026, as team lead"),
        ("Top 100", "Smart Interviews assessments", "of 4,000+ KL University students; A batch"),
        ("AWS", "Certified Cloud Practitioner", "2026 · GitHub Copilot certified, 2025"),
    ]
    right = []
    for i, (lead, title, sub) in enumerate(rows):
        y = 132 + i * 66
        right.append(f'<g class="rise" {delay(.3 + i * .1)}>'
                     f'{display(660, y + 20, lead, 38, ON_OR)}'
                     f'<text x="830" y="{y + 4}" class="sn b" font-size="16" fill="{ON_OR_DEEP}">{esc(title)}</text>'
                     f'<text x="830" y="{y + 26}" class="sn" font-size="14.5" fill="{ON_OR_DEEP}">{esc(sub)}</text>'
                     + (f'<line x1="660" x2="1136" y1="{y + 44.5}" y2="{y + 44.5}" stroke="{ON_OR}" stroke-opacity=".18"/>' if i < len(rows) - 1 else "")
                     + "</g>")
    body = f"""{bg}
{display(64, 72, "Experience & highlights", 40, ON_OR, "rise")}
<text x="1136" y="70" class="sn b rise" font-size="15" fill="{ON_OR_DEEP}" text-anchor="end" {delay(.1)}>B.Tech CSE, 2024 – 2028</text>
<g class="rise" {delay(.2)}>{''.join(left)}</g>
<line x1="612" x2="612" y1="112" y2="{h - 48}" stroke="{ON_OR}" stroke-opacity=".18" class="fade" {delay(.3)}/>
{''.join(right)}
{border}"""
    return svg(w, h, body, "Experience and highlights: Engineer Intern at Espaze.in; AIR 3 in the Entrepreneurship Development Drive; "
               "1st at Enigma, IIT Madras; top 100 in Smart Interviews; AWS Certified Cloud Practitioner.",
               ["serif-i", "sans", "sans-b"], "", gdefs)


def stack():
    groups = [
        ("LANGUAGES", ["Java", "Python", "JavaScript", "TypeScript", "SQL", "Go", "C", "Kotlin", "Bash"]),
        ("BACKEND & WEB", ["Spring Boot", "Spring Security", "Node.js", "Express", "FastAPI", "REST", "JWT", "React", "Tailwind CSS"]),
        ("DATA & CLOUD", ["MySQL", "PostgreSQL", "SQLite", "MongoDB", "AWS", "Google Cloud", "Docker", "GitHub Actions"]),
        ("SECURITY & TOOLS", ["MITRE ATT&CK", "Kali Linux", "Solidity", "Git", "Maven", "Swagger / OpenAPI", "pytest"]),
    ]
    x0, x1, row_h = 300, 1136, 46
    rows, y = [], 118
    for gi, (label, items) in enumerate(groups):
        rows.append(f'<text x="64" y="{y + 19}" class="cap" fill="{DIM}" style="font-size:11px;letter-spacing:2px">{esc(label)}</text>')
        x = x0
        for i, item in enumerate(items):
            _, cw = chip(0, 0, item)
            if x + cw > x1:
                x, y = x0, y + 38
            part, cw = chip(x, y, item)
            rows.append(f'<g class="rise" {delay(.2 + gi * .12 + i * .03)}>{part}</g>')
            x += cw + 8
        y += row_h + 8
    h = y + 24
    gdefs, bg, border = frame(1200, h)
    body = f"""{bg}
{display(64, 72, "Stack", 40, ORANGE, "rise")}
{''.join(rows)}
{border}"""
    return svg(1200, h, body, "Stack: " + "; ".join(f"{label.title()}: {', '.join(items)}" for label, items in groups),
               ["serif-i", "sans", "sans-b"], "", gdefs)


# ---------------------------------------------------------------- main

def main():
    days = load_contributions()
    s = stats(days)
    CARDS.mkdir(exist_ok=True)
    cards = {
        "hero.svg": hero(),
        "graph.svg": graph(s, days),
        "activity.svg": activity(s),
        "tile-backend.svg": tile("01", "BACKEND", 50, "", ["REST endpoints behind a", "4-role hospital platform"],
                                 "HealthCore", " · Spring Boot", .1),
        "tile-security.svg": tile("02", "SECURITY", 16, "", ["MITRE ATT&CK techniques", "caught by 23 detection rules"],
                                  "Detection Engine", " · Python, FastAPI", .25, tone="orange"),
        "tile-dsa.svg": tile("03", "PROBLEM SOLVING", 238, "+", ["problems solved on CodeChef", "across 48 contests"],
                             "CodeChef", " · peak rating 1432", .4),
        "repo-healthcore.svg": repo_card("HealthCore", "JAVA · FULL-STACK",
            "Hospital platform for patients, doctors, pharmacists and admins on a 50-endpoint Spring Boot API with JWT and role-based access.",
            ["Spring Boot", "Spring Security", "React", "MySQL", "Docker"], .1),
        "repo-detection.svg": repo_card("Behavioral Detection Engine", "PYTHON · SECURITY",
            "Catches simulated attacks across 16 MITRE ATT&CK techniques with per-host risk scoring and a React triage dashboard.",
            ["FastAPI", "SQLAlchemy", "React", "Recharts"], .2),
        "repo-driftbound.svg": repo_card("DriftBound", "PYTHON · ML · HACKATHON",
            "Streaming classifier that detects concept drift and relearns to zero error in 10 labels, with a live adversary that shows where it breaks.",
            ["FastAPI", "PostgreSQL", "React", "TypeScript", "pytest"], .3),
        "repo-planner.svg": repo_card("Planner", "NODE.JS · PWA",
            "Daily planner with monthly goals, repeating tasks and push reminders that pulls in KL University LMS work and Gmail or Outlook mail.",
            ["Node.js", "Express", "SQLite", "Preact", "GitHub Actions"], .4),
        "highlights.svg": highlights(),
        "stack.svg": stack(),
    }
    for name, content in cards.items():
        (CARDS / name).write_text(content)
        print(f"{name:22} {len(content) / 1024:6.1f} KB")
    print(f"{LOGIN}: total {s['total']} · active {s['active']} · streak {s['longest']} · busiest {s['busiest'][0]} on {s['busiest'][1]}")


if __name__ == "__main__":
    main()
