"""Derive the site's brand assets from the masters in brand/.

Run after replacing a master:

    .venv/bin/python scripts/build_brand_assets.py

The masters are large RGBA PNGs. The site needs small, tightly-cropped,
retina-ready versions -- plus a light-theme wordmark, because the supplied
wordmark is white and would be invisible on the page's chart-paper ground.
Recolouring is done here, once, rather than with CSS filters at runtime.

Requires Pillow (dev only; the server never runs this).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand"
STATIC = ROOT / "app" / "web" / "static"

MARK_MASTER = BRAND / "failecho-logo-mark-separated.png"
LOCKUP_MASTER = BRAND / "failecho-wordmark-separated.png"

#: Page ink, matching --ink in style.css.
INK = (23, 33, 29)

#: Google will not show a favicon in search results unless it is square and,
#: per its documentation, ideally a multiple of 48px. The mark is 1.12:1, so
#: it gets padded rather than squashed.
FAVICON_SQUARE_PX = 144

#: iOS home-screen icon. Apple composites a transparent icon onto black and
#: rounds the corners itself, so this one is rendered square and opaque on the
#: page background. Google also accepts rel="apple-touch-icon" as a favicon
#: source, which means it has to satisfy the same square rule as favicon.png.
APPLE_TOUCH_PX = 192

#: Retina: every asset is rendered at twice its largest CSS size.
MARK_PX = 128          # displayed up to 64
LOCKUP_PX = 640        # displayed up to ~320 wide
FAVICON_PX = 64

#: Page ground, matching --bg in style.css. Used behind the opaque icons.
BACKDROP = (13, 14, 16)

#: Chart-paper ground, matching --paper in style.css.
PAPER = (239, 241, 236)
MUTED = (74, 87, 79)

#: Social card. Most platforms will not render an SVG preview, so this one is
#: a real PNG.
OG_SIZE = (1200, 630)

#: Palette size used when quantising. The lockup is flat-colour type plus one
#: small gradient mark, so a modest palette cuts the file by ~4x with no
#: visible banding -- worth it when the whole page is otherwise 10 KB.
PALETTE = 96


def trim(image: Image.Image) -> Image.Image:
    """Crop away fully transparent padding so alignment is predictable."""
    box = image.getchannel("A").getbbox()
    return image.crop(box) if box else image


#: Alpha at or below this counts as empty when measuring the mark. The master's
#: anti-aliasing leaves faint pixels right out to the canvas edge; measured with
#: alpha > 0 the "content" was the whole canvas, trimming did nothing, and any
#: offset in the file went straight into the favicon.
SOLID_ALPHA = 16


def trim_solid(image: Image.Image) -> Image.Image:
    """Crop to the visible mark, ignoring faint anti-aliasing at the edges."""
    box = image.getchannel("A").point(lambda a: 255 if a > SOLID_ALPHA else 0).getbbox()
    return image.crop(box) if box else image


def fit_height(image: Image.Image, height: int) -> Image.Image:
    width = max(1, round(image.width * height / image.height))
    return image.resize((width, height), Image.LANCZOS)


def fit_width(image: Image.Image, width: int) -> Image.Image:
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.LANCZOS)


def square(image: Image.Image, size: int, margin: float = 0.08) -> Image.Image:
    """Centre the mark on a transparent square canvas.

    Padding, never stretching: a distorted logo is worse than a small one. The
    margin keeps the strokes off the edge, where browsers and search results
    tend to crop or round the corners.
    """
    inner = round(size * (1 - 2 * margin))
    scaled = (
        fit_width(image, inner) if image.width >= image.height
        else fit_height(image, inner)
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(
        scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2)
    )
    return canvas


def opaque(image: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """Flatten a transparent icon onto a solid ground.

    iOS does not honour transparency in a home-screen icon; it fills the gaps
    with black and rounds the corners. Compositing here means the icon looks
    the same everywhere instead of depending on the platform's guess.
    """
    canvas = Image.new("RGBA", image.size, (*colour, 255))
    canvas.alpha_composite(image)
    return canvas


def recolour_neutrals(image: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """Repaint the white glyphs as ink, leaving the red mark alone.

    A pixel counts as "glyph" when it is close to neutral (low saturation).
    The mark's reds are strongly saturated, so they survive untouched and the
    lockup keeps its accent in both themes.
    """
    out = image.copy()
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            if max(r, g, b) - min(r, g, b) < 40:  # neutral: white/grey glyph
                # Preserve the glyph's own anti-aliasing by keeping its alpha.
                pixels[x, y] = (*colour, a)
    return out


def save(image: Image.Image, name: str, palette: int | None = None) -> None:
    path = STATIC / name
    if palette:
        image = image.quantize(colors=palette, method=Image.FASTOCTREE).convert("RGBA")
    image.save(path, "PNG", optimize=True)
    print(f"{name:<26} {image.width}x{image.height}  {path.stat().st_size / 1024:.1f} KB")


def build_og_card(lockup: Image.Image) -> Image.Image:
    """The social preview: lockup, tagline, category line, domain."""
    card = Image.new("RGBA", OG_SIZE, (*PAPER, 255))
    draw = ImageDraw.Draw(card)
    draw.rectangle([0, 0, OG_SIZE[0], 6], fill=(*INK, 255))

    art = fit_width(recolour_neutrals(lockup, INK), 520)
    card.alpha_composite(art, (96, 92))

    serif = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
    sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    headline = ImageFont.truetype(serif, 62)
    body = ImageFont.truetype(sans, 30)
    small = ImageFont.truetype(sans, 24)

    draw.text((96, 300), "Before you retry,", font=headline, fill=(*INK, 255))
    draw.text((96, 374), "check the echo.", font=headline, fill=(*INK, 255))
    draw.rectangle([96, 470, 99, 556], fill=(192, 52, 29, 255))
    draw.text((124, 470), "Live failure intelligence", font=body, fill=(*MUTED, 255))
    draw.text((124, 512), "for autonomous software.", font=body, fill=(*MUTED, 255))
    draw.text((96, 578), "failecho.com", font=small, fill=(*MUTED, 255))
    return card


def main() -> None:
    mark = trim_solid(Image.open(MARK_MASTER).convert("RGBA"))
    lockup = trim(Image.open(LOCKUP_MASTER).convert("RGBA"))

    save(fit_height(mark, MARK_PX), "logo.png", PALETTE)
    save(square(mark, FAVICON_SQUARE_PX), "favicon.png", PALETTE)
    save(
        opaque(square(mark, APPLE_TOUCH_PX, margin=0.14), BACKDROP),
        "apple-touch-icon.png",
        PALETTE,
    )

    # The master is white-on-transparent: correct for dark, invisible on light.
    save(fit_width(lockup, LOCKUP_PX), "wordmark-dark.png", PALETTE)
    save(
        fit_width(recolour_neutrals(lockup, INK), LOCKUP_PX),
        "wordmark-light.png",
        PALETTE,
    )
    save(build_og_card(lockup), "og-image.png", 128)


if __name__ == "__main__":
    main()
