"""
LZRD icon asset generator
=========================
Generates the PWA PNG icons (192×192 and 512×512) used by the web manifest
and service-worker app shell.  Run this script any time the icon design or
colour theme changes:

    python scripts/generate_icons.py

Output files:
    web/icons/icon-192.png
    web/icons/icon-512.png

Requirements:  Pillow  (pip install Pillow)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

# Deep graphite — matches CSS var(--bg) #1a1a1a
_BG = (26, 26, 26, 255)

# Lizard green — matches CSS var(--green) #6DBF4A
_GREEN: tuple[int, int, int] = (109, 191, 74)

# ---------------------------------------------------------------------------
# Icon sizes to generate
# ---------------------------------------------------------------------------

SIZES = (192, 512)

# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def make_icon(size: int) -> Image.Image:
    """Return a Pillow image of the LZRD lizard icon at *size* × *size* pixels.

    The design mirrors ``web/icon.svg``: a stylised side-profile lizard facing
    right, drawn in lizard green on a graphite rounded-square background.
    """
    img = Image.new("RGBA", (size, size), _BG)
    draw = ImageDraw.Draw(img)
    c = _GREEN

    # Scale factor: all design coordinates are in a 64×64 unit space.
    def sc(v: float) -> int:
        return int(v * size / 64)

    # Background rounded square with slight graphite tone is already the
    # image background.  A very subtle lighter border adds depth at large sizes.
    if size >= 192:
        draw.rounded_rectangle(
            [sc(1), sc(1), sc(63), sc(63)],
            radius=sc(12),
            outline=(40, 40, 40, 255),
            width=max(1, sc(1)),
        )

    # ── Lizard body ─────────────────────────────────────────────────────────
    draw.ellipse([sc(18), sc(24), sc(48), sc(42)], fill=c)

    # ── Head ────────────────────────────────────────────────────────────────
    draw.ellipse([sc(38), sc(20), sc(56), sc(34)], fill=c)

    # ── Snout (triangle pointing right) ─────────────────────────────────────
    draw.polygon(
        [(sc(53), sc(25)), (sc(63), sc(29)), (sc(53), sc(33))],
        fill=c,
    )

    # ── Eye ─────────────────────────────────────────────────────────────────
    draw.ellipse([sc(50), sc(22), sc(55), sc(27)], fill=(255, 255, 255))
    draw.ellipse([sc(51), sc(23), sc(54), sc(26)], fill=_BG[:3])

    # ── Tail ────────────────────────────────────────────────────────────────
    lw = max(2, sc(4))
    draw.line([(sc(18), sc(34)), (sc(10), sc(40)), (sc(4), sc(52))], fill=c, width=lw)

    # ── Legs (rear/front × upper/lower) ─────────────────────────────────────
    leg_w = max(1, sc(3))
    draw.line([(sc(25), sc(26)), (sc(19), sc(17))], fill=c, width=leg_w)
    draw.line([(sc(25), sc(40)), (sc(19), sc(50))], fill=c, width=leg_w)
    draw.line([(sc(39), sc(25)), (sc(45), sc(16))], fill=c, width=leg_w)
    draw.line([(sc(39), sc(40)), (sc(45), sc(50))], fill=c, width=leg_w)

    return img


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    icons_dir = repo_root / "web" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    for px in SIZES:
        dest = icons_dir / f"icon-{px}.png"
        make_icon(px).save(str(dest))
        print(f"  ✓  {dest.relative_to(repo_root)}  ({px}×{px})")

    print("Done.")


if __name__ == "__main__":
    main()
