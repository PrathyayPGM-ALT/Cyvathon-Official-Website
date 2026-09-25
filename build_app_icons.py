"""Draw the Cyvathon app icons.

The home-screen icon is the site's own mark — a blue ring around a white C on
near-black — redrawn at the sizes phones ask for. Colours are read from
static/apple-touch-icon.png so the app icon always matches the site's.

    python build_app_icons.py

writes static/icons/icon-192.png, icon-512.png (rounded, for anywhere) and
icon-maskable-512.png (full-bleed, for Android, which crops it to its own
shape — so the mark sits well inside the middle 80%).
"""
import os
from collections import Counter
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "static", "apple-touch-icon.png")
OUT = os.path.join(HERE, "static", "icons")
MASTER = 1024
FONTS = ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def palette():
    """Background, ring and letter colours, read off the existing icon."""
    im = Image.open(SRC).convert("RGBA")
    px = [p[:3] for p in im.getdata() if p[3] > 250]
    bright = lambda c: sum(c) / 3
    dark = Counter(c for c in px if bright(c) < 40).most_common(1)[0][0]
    blue = Counter(c for c in px if c[2] > 200 and c[2] - c[0] > 80).most_common(1)[0][0]
    white = Counter(c for c in px if bright(c) > 215).most_common(1)[0][0]
    return dark, blue, white


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def mark(size, ring_r, bg, blue, white, rounded):
    """The C-in-a-ring, drawn big and scaled down so the edges stay smooth."""
    S = MASTER
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if rounded:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=bg)
    else:
        d.rectangle([0, 0, S, S], fill=bg)
    c, r, w = S / 2, S * ring_r, S * ring_r * 0.24
    d.ellipse([c - r, c - r, c + r, c + r], fill=blue)
    d.ellipse([c - r + w, c - r + w, c + r - w, c + r - w], fill=bg)
    f = font(int(S * ring_r * 1.2))
    box = d.textbbox((0, 0), "C", font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text((c - tw / 2 - box[0], c - th / 2 - box[1]), "C", font=f, fill=white)
    return im.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    bg, blue, white = palette()
    print("palette", bg, blue, white)
    for size in (192, 512):
        mark(size, 0.315, bg, blue, white, rounded=True).save(os.path.join(OUT, f"icon-{size}.png"))
    # Android crops maskable icons to a circle or squircle: keep the mark
    # inside the safe zone, and let the background run to every edge.
    mark(512, 0.25, bg, blue, white, rounded=False).save(os.path.join(OUT, "icon-maskable-512.png"))
    print("wrote", sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
