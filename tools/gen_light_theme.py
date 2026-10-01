"""Erzeugt cko_ibs/static/light.css aus den dunklen Stylesheets.

Jede Regel mit Farbwerten wird unter :root[data-theme="light"] mit umgerechneten Farben wiederholt:
dunkle Flächen werden hell, helle Schrift dunkel, Akzentfarben bleiben im Farbton und werden kontraststärker.
Handkorrekturen stehen in cko_ibs/static/light-overrides.css (wird angehängt).

Aufruf: python tools/gen_light_theme.py
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "cko_ibs" / "static"
SOURCES = ["styles.css", "connection.css", "branding.css", "sco.css"]
PREFIX = ':root[data-theme="light"]'
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\b(?:white|black|transparent)\b")
COLOR_PROPS = ("color", "background", "background-color", "background-image", "border", "border-color",
               "border-top", "border-right", "border-bottom", "border-left", "border-left-color", "outline",
               "box-shadow", "text-shadow", "fill", "stroke", "caret-color", "accent-color", "--")
SHADOW_PROPS = ("box-shadow", "text-shadow")
TEXT_PROPS = ("color", "fill", "caret-color")


def parse_color(token: str) -> tuple[float, float, float, float] | None:
    token = token.strip().lower()
    if token == "transparent":
        return None
    if token == "white":
        return 1, 1, 1, 1
    if token == "black":
        return 0, 0, 0, 1
    if token.startswith("#"):
        value = token[1:]
        if len(value) in (3, 4):
            value = "".join(ch * 2 for ch in value)
        r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
        a = int(value[6:8], 16) / 255 if len(value) == 8 else 1
        return r, g, b, a
    numbers = [part.strip() for part in token[token.index("(") + 1:-1].replace("/", ",").split(",")]
    r, g, b = (float(x) / 255 for x in numbers[:3])
    a = float(numbers[3]) if len(numbers) > 3 else 1
    return r, g, b, a


def fmt(r: float, g: float, b: float, a: float) -> str:
    rgb = tuple(max(0, min(255, round(x * 255))) for x in (r, g, b))
    if a >= 0.999:
        return "#{:02x}{:02x}{:02x}".format(*rgb)
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {round(a, 3)})"


def is_accent(token: str) -> bool:
    parsed = parse_color(token)
    if parsed is None:
        return False
    _hue, light, s = colorsys.rgb_to_hls(*parsed[:3])
    return s > 0.35 and 0.3 <= light <= 0.75 and parsed[3] > 0.5


def convert(token: str, prop: str, on_accent: bool = False) -> str:
    parsed = parse_color(token)
    if parsed is None:
        return token
    r, g, b, a = parsed
    if on_accent and prop in TEXT_PROPS and colorsys.rgb_to_hls(r, g, b)[1] > 0.75:
        # Schrift auf farbiger Fläche (Buttons, Badges mit Vollfarbe) bleibt hell
        return fmt(1, 1, 1, a)
    if prop in SHADOW_PROPS:
        # Schatten bleiben dunkel, aber deutlich zarter; Leuchteffekte (helle/farbige Schatten) fast unsichtbar
        h, light, s = colorsys.rgb_to_hls(r, g, b)
        return fmt(0.06, 0.12, 0.2, a * (0.35 if light < 0.3 else 0.12))
    h, light, s = colorsys.rgb_to_hls(r, g, b)
    if s > 0.35 and 0.3 <= light <= 0.75:
        # Akzentfarbe: Farbton halten. Schrift kräftig, Rahmen zart, Flächen mittel
        if prop in TEXT_PROPS or prop.startswith("--"):
            new_light = max(0.26, min(0.38, light * 0.62))
        elif prop.startswith(("border", "outline")):
            new_light = 0.7
        else:
            new_light = max(0.32, min(0.46, light * 0.75))
        r, g, b = colorsys.hls_to_rgb(h, new_light, min(1, s * 1.05))
        return fmt(r, g, b, a)
    if light < 0.3:
        # dunkle Fläche → helle Fläche; leicht getönt, Abstufungen bleiben erhalten
        new_light = 1 - light * 0.55
        new_s = min(s, 0.45) * (0.6 if prop in TEXT_PROPS else 1)
        if prop in TEXT_PROPS:
            new_light = 0.18 + light * 0.3
        if a < 0.999 and prop not in TEXT_PROPS:
            # halbtransparente dunkle Tönung → dezente dunkle Tönung auf Hell
            return fmt(*colorsys.hls_to_rgb(h, 0.35, min(s, 0.5)), round(a * 0.45, 3))
        r, g, b = colorsys.hls_to_rgb(h, new_light, new_s)
        return fmt(r, g, b, a)
    if light > 0.75:
        if prop in TEXT_PROPS or prop.startswith("--"):
            # helle Schrift → dunkle Schrift
            new_light = 0.12 + (1 - light) * 0.9
            r, g, b = colorsys.hls_to_rgb(h, new_light, min(s, 0.55))
            return fmt(r, g, b, a)
        if a < 0.999:
            # helle, halbtransparente Linie/Fläche → dunkle Entsprechung
            return fmt(*colorsys.hls_to_rgb(h, 0.25, min(s, 0.4)), round(a * 0.9, 3))
        r, g, b = colorsys.hls_to_rgb(h, 1 - (light - 0.75) * 0.3 - 0.05, min(s, 0.4))
        return fmt(r, g, b, a)
    # mittlere, wenig gesättigte Töne (Grau): spiegeln
    r, g, b = colorsys.hls_to_rgb(h, 1 - light if prop not in TEXT_PROPS else min(0.42, 1 - light), s)
    return fmt(r, g, b, a)


def blocks(css: str):
    """(prelude, body) auf oberster Ebene; @media-Blöcke werden rekursiv geliefert."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    i, n = 0, len(css)
    while i < n:
        start = css.find("{", i)
        if start < 0:
            return
        prelude = css[i:start].strip()
        depth, j = 1, start + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        yield prelude, css[start + 1:j - 1]
        i = j


def prefix_selectors(prelude: str) -> str:
    parts = []
    for selector in prelude.split(","):
        selector = selector.strip()
        if selector in (":root", "html"):
            parts.append(PREFIX)
        else:
            parts.append(f"{PREFIX} {selector}")
    return ", ".join(parts)


def convert_rule(prelude: str, body: str) -> str | None:
    declarations = []
    on_accent = any(prop.strip().lower().startswith("background") and any(is_accent(m.group(0)) for m in COLOR.finditer(value))
                    for prop, value in (d.split(":", 1) for d in body.split(";") if ":" in d))
    for declaration in body.split(";"):
        if ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        prop = prop.strip().lower()
        if not (prop.startswith(COLOR_PROPS) or prop.startswith("--")) or not COLOR.search(value):
            continue
        converted = COLOR.sub(lambda match, prop=prop: convert(match.group(0), prop, on_accent), value.strip())
        declarations.append(f"{prop}:{converted}")
    if not declarations:
        return None
    return f"{prefix_selectors(prelude)}{{{';'.join(declarations)}}}"


def convert_css(css: str) -> list[str]:
    out = []
    for prelude, body in blocks(css):
        if prelude.startswith("@media"):
            inner = [rule for rule in (convert_rule(p, b) for p, b in blocks(body)) if rule]
            if inner:
                out.append(f"{prelude}{{{''.join(inner)}}}")
        elif prelude.startswith("@"):
            continue
        else:
            rule = convert_rule(prelude, body)
            if rule:
                out.append(rule)
    return out


def main() -> None:
    lines = ["/* Automatisch erzeugt mit tools/gen_light_theme.py – nicht von Hand bearbeiten. */",
             f"{PREFIX}{{color-scheme:light}}"]
    for name in SOURCES:
        lines.append(f"/* {name} */")
        lines.extend(convert_css((STATIC / name).read_text(encoding="utf-8")))
    overrides = STATIC / "light-overrides.css"
    if overrides.exists():
        lines.append("/* light-overrides.css */")
        lines.append(overrides.read_text(encoding="utf-8").strip())
    (STATIC / "light.css").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"light.css: {len(lines)} Zeilen")


if __name__ == "__main__":
    main()
