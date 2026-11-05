#!/usr/bin/env python3
"""Generate the README hero images, one per language and color theme. Run from anywhere; needs only Python 3."""

import base64
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "images" / "readme"

SERIF = "'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif"
SANS = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"
MONO = f"'JetBrains Mono',SFMono-Regular,Menlo,Consolas,'Liberation Mono',{SANS}"

THEMES = {
    "light": dict(bg="#fbfdfb", line="rgba(18,38,29,.12)", soft="rgba(18,38,29,.07)", ink="#12261d", accent="#1b7f52",
                  text="#43574d", chip="#ffffff", well="#e9f4ee", muted="#6b7d73"),
    "dark": dict(bg="#141c18", line="rgba(255,255,255,.12)", soft="rgba(255,255,255,.08)", ink="#eaf2ed",
                 accent="#4cc792", text="#a9bab1", chip="#1c2622", well="#223329", muted="#8da198"),
}

ICONS = {
    "team": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.5-4 3-6 6.5-6s6 2 6.5 6"/><path d="M16 4.800a3.5 3.5 0 0 1 0 6.400M18 14.500c2 .8 3.2 2.8 3.5 5.5"/>',
    "seal": '<path d="M12 2.5 20 6v6c0 5-3.4 8-8 9.500C7.4 20 4 17 4 12V6l8-3.500Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    "gate": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M12 3v18M8 12h.01M16 12h.01"/>',
    "repair": '<path d="M14.5 6.500a4.5 4.5 0 0 1 5.8 5.800l-2.3-2.3-2.5.5-.5 2.5 2.3 2.300a4.5 4.5 0 0 1-5.8-5.800L5 16.2 7.8 19l6.7-6.7"/>',
    "box": '<path d="m12 2.5 9 5v9l-9 5-9-5v-9l9-5Z"/><path d="m3 7.5 9 5 9-5M12 12.500v9"/>',
    "terminal": '<rect x="2.5" y="4" width="19" height="16" rx="2"/><path d="m7 9 3.5 3L7 15M13 15h4"/>',
    "refresh": '<path d="M20.5 11A8.5 8.5 0 0 0 5 6.500L3 9m0-5v5h5"/><path d="M3.5 13A8.5 8.5 0 0 0 19 17.500l2-2.500m0 5v-5h-5"/>',
    "levels": '<path d="M4 20V12M12 20V8M20 20V4"/><path d="M2 20h20"/>',
    "cube": '<path d="M12 2.5 4 7v10l8 4.5 8-4.500V7l-8-4.500Z"/><path d="m4 7 8 4.500L20 7M12 11.500v10"/>',
    "swap": '<path d="M4 8h14l-4-4M20 16H6l4 4"/>',
    "chat": '<path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 9h8M8 12.500h5"/>',
    "doc": '<path d="M6 2.500h8l5 5v14H6v-19Z"/><path d="M14 2.500v5h5M9 13h7M9 17h7"/>',
    "report": '<path d="M4 20V4h16v16H4Z"/><path d="M8 16v-4M12 16V8M16 16v-6"/>',
    "tag": '<path d="M3 3h9l9 9-9 9-9-9V3Z"/><path d="M7.5 7.500h.01"/>',
    "contrast": '<circle cx="12" cy="12" r="9"/><path d="M12 3v18a9 9 0 0 0 0-18Z"/>',
    "github": '<path d="M9 19c-4.3 1.3-4.3-2.2-6-2.700m12 5v-3.500c0-1 .1-1.4-.5-2 3.4-.4 7-1.7 7-7.500a5.7 5.7 0 0 0-1.5-4 5.4 5.4 0 0 0-.1-4S18.6-.1 15.5 2a14 14 0 0 0-7 0C5.4-.1 4.1.300 4.1.3a5.4 5.4 0 0 0-.1 4 5.7 5.7 0 0 0-1.5 4c0 5.8 3.6 7.1 7 7.5-.6.6-.6 1.2-.5 2v3.5"/>',
    "laptop": '<rect x="4" y="4" width="16" height="12" rx="1.5"/><path d="M2 20h20"/>',
    "layers": '<path d="m12 2 10 5-10 5L2 7l10-5ZM2 12l10 5 10-5M2 17l10 5 10-5"/>',
}


WORDMARK = "Siamang"
CHIP_ICONS = ["team", "seal", "gate", "repair", "cube", "terminal", "refresh", "levels"]
FOOT_ICONS = ["github", "laptop", "layers"]
CONTENT = {
    "en": {
        "eyebrow": "EVIDENCE-BASED AUTONOMOUS SOFTWARE ENGINEERING",
        "h1": "Autonomous engineering.",
        "h2": "Every claim backed by evidence.",
        "sub": ["An AI engineering team that plans, writes code and opens pull", "requests, and shows where every result came from."],
        "chips": ["Agent team", "Sealed evidence", "Approval gates", "CI repair", "3D web interface", "Sandbox · opt-in", "Recovery · opt-in", "Autonomy · opt-in"],
        "foot": ["Open source · MIT License", "Runs on your machine", "Claude CLI or Ollama"],
    },
    "id": {
        "eyebrow": "REKAYASA SOFTWARE OTONOM BERBASIS BUKTI",
        "h1": "Rekayasa otonom.",
        "h2": "Setiap klaim ada buktinya.",
        "sub": ["Tim engineering AI yang merencanakan, menulis kode, dan membuka", "pull request, serta menunjukkan asal setiap hasil."],
        "chips": ["Tim agen", "Bukti tersegel", "Gerbang setuju", "Perbaikan CI", "Antarmuka web 3D", "Sandbox · opsional", "Recovery · opsional", "Otonomi · opsional"],
        "foot": ["Open source · Lisensi MIT", "Berjalan di komputer Anda", "Claude CLI atau Ollama"],
    },
}


def text(x, y, value, size, fill, family=SANS, extra=""):
    return f'<text x="{x}" y="{y}" font-family="{escape(family, quote=True)}" font-size="{size}" fill="{fill}"{extra}>{escape(value)}</text>'


def icon(name, x, y, color, scale=1.0):
    return (
        f'<g transform="translate({x} {y}) scale({scale})" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>'
    )


def hero(content, theme, logo, wordmark, chip_icons, foot_icons):
    t = THEMES[theme]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1600 662" '
        f'width="1600" height="662" role="img" aria-label="{escape(wordmark, quote=True)}">',
        f'<defs><clipPath id="logo"><rect x="72" y="52" width="76" height="76" rx="16"/></clipPath></defs>',
        f'<rect x="1" y="1" width="1598" height="660" rx="20" fill="{t["bg"]}" stroke="{t["line"]}" stroke-width="2"/>',
        f'<image href="data:image/png;base64,{logo}" x="72" y="52" width="76" height="76" clip-path="url(#logo)"/>',
        text(166, 102, wordmark, 34, t["ink"], SANS, ' font-weight="600"'),
        text(72, 192, content["eyebrow"], 20, t["accent"], MONO, ' letter-spacing="3"'),
        text(68, 282, content["h1"], content.get("size", 58), t["ink"], SERIF),
        text(68, 366, content["h2"], content.get("size", 58), t["accent"], SERIF),
    ]
    for i, line in enumerate(content["sub"]):
        parts.append(text(72, 438 + i * 42, line, 27, t["text"]))
    for i, label in enumerate(content["chips"]):
        x, y = 1002 + (i % 2) * 270, 178 + (i // 2) * 74
        parts += [
            f'<rect x="{x}" y="{y}" width="256" height="62" rx="12" fill="{t["chip"]}" stroke="{t["line"]}" stroke-width="2"/>',
            f'<rect x="{x + 11}" y="{y + 11}" width="40" height="40" rx="9" fill="{t["well"]}"/>',
            icon(chip_icons[i], x + 19, y + 19, t["accent"]),
            text(x + 62, y + 38, label, 19, t["ink"], SANS, ' font-weight="600"'),
        ]
    parts.append(f'<path d="M72 558H1528" stroke="{t["soft"]}" stroke-width="2"/>')
    for i, label in enumerate(content["foot"]):
        x = 72 + i * 485
        parts += [icon(foot_icons[i], x, 592, t["muted"], 1.08), text(x + 40, 613, label, 22, t["text"])]
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    logo = base64.b64encode((OUT / "logo.png").read_bytes()).decode()
    for lang, content in CONTENT.items():
        for theme in THEMES:
            svg = hero(content, theme, logo, WORDMARK, CHIP_ICONS, FOOT_ICONS)
            (OUT / f"hero-{lang}-{theme}.svg").write_text(svg, encoding="utf-8")
    print(f"Wrote {len(CONTENT) * len(THEMES)} images to {OUT}")

if __name__ == "__main__":
    main()
