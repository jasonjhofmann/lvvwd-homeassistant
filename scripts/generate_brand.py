#!/usr/bin/env python3
"""Generate brand assets (original artwork, no third-party logos).

Produces custom_components/lvvwd/brand/{icon,icon@2x,logo,logo@2x}.png per the
home-assistant/brands spec: icon 256x256 (+@2x 512), logo landscape (+@2x). The
glyph is an ORIGINAL water droplet — a generic blue teardrop with a highlight,
NOT the LVVWD or SNWA logo (this is an unofficial community integration). HA
2026.3+ serves these via the local brands proxy.

Usage: python3 scripts/generate_brand.py   (requires Pillow)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "custom_components" / "lvvwd" / "brand"

DEEP = (13, 71, 161, 255)  # deep water blue (tile)
DROP = (33, 150, 243, 255)  # droplet body
DROP_HI = (129, 212, 250, 255)  # droplet highlight / specular
DARK_TEXT = (15, 40, 75, 255)
WHITE = (255, 255, 255, 255)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _droplet(d: ImageDraw.ImageDraw, cx: float, cy: float, h: float) -> None:
    """Draw a classic teardrop centered horizontally at cx, spanning height h.

    The drop is a circle for the lower bulb plus a tapering triangular point on
    top — a pure geometric water droplet, no external mark."""
    r = h * 0.32  # bulb radius
    bulb_cy = cy + h * 0.16  # bulb center sits in the lower portion
    tip_y = cy - h * 0.42  # the point
    # Tapering point: a triangle from just above the bulb up to the tip, its
    # base width = a chord of the bulb so the silhouette is continuous.
    base_half = r * 0.74
    base_y = bulb_cy - r * 0.62
    d.polygon(
        [(cx, tip_y), (cx - base_half, base_y), (cx + base_half, base_y)],
        fill=DROP,
    )
    d.ellipse([cx - r, bulb_cy - r, cx + r, bulb_cy + r], fill=DROP)
    # Specular highlight: a small offset ellipse on the upper-left of the bulb.
    hr = r * 0.30
    hx, hy = cx - r * 0.34, bulb_cy - r * 0.30
    d.ellipse([hx - hr, hy - hr * 1.4, hx + hr, hy + hr * 1.4], fill=DROP_HI)


def draw_glyph(size: int) -> Image.Image:
    """A white water droplet on a rounded deep-blue tile."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 512  # design at 512

    d.rounded_rectangle([16 * s, 16 * s, 496 * s, 496 * s], radius=96 * s, fill=DEEP)
    _droplet(d, 256 * s, 268 * s, 300 * s)
    return img


def draw_logo(height: int, dark: bool) -> Image.Image:
    glyph = draw_glyph(height)
    font = _font(int(height * 0.30))
    text = "LVVWD"
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tw = int(probe.textlength(text, font=font))
    pad = int(height * 0.16)
    img = Image.new("RGBA", (height + pad + tw + pad, height), (0, 0, 0, 0))
    img.paste(glyph, (0, 0), glyph)
    d = ImageDraw.Draw(img)
    d.text(
        (height + pad, height // 2),
        text,
        font=font,
        fill=WHITE if dark else DARK_TEXT,
        anchor="lm",
    )
    return img


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    draw_glyph(512).save(BRAND / "icon@2x.png")
    draw_glyph(512).resize((256, 256), Image.LANCZOS).save(BRAND / "icon.png")
    big = draw_logo(256, dark=False)
    big.save(BRAND / "logo@2x.png")
    big.resize((big.width // 2, 128), Image.LANCZOS).save(BRAND / "logo.png")
    for f in sorted(BRAND.iterdir()):
        print(f.name, Image.open(f).size)


if __name__ == "__main__":
    main()
