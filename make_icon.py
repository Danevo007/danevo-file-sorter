#!/usr/bin/env python3
"""Generates danevo.png and danevo.ico (multi-size) for Danevo File Sorter.
Requires: pip install pillow
"""
from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024  # draw big, shrink for crisp edges


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def build() -> Image.Image:
    # gradient background (top-left light violet -> bottom-right deep indigo)
    v = Image.linear_gradient("L").resize((S, S))
    grad = ImageChops.add(v, v.transpose(Image.TRANSPOSE), scale=2.0)  # smooth diagonal blend
    top, bottom = (150, 130, 255), (72, 52, 205)
    bg = Image.composite(Image.new("RGB", (S, S), bottom), Image.new("RGB", (S, S), top), grad).convert("RGBA")
    # soft glow
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-200, -300, 800, 500), fill=(255, 255, 255, 55))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))

    # folder shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((190, 400, 854, 830), radius=80, fill=(20, 10, 90, 150))
    bg.alpha_composite(sh.filter(ImageFilter.GaussianBlur(28)))

    d = ImageDraw.Draw(bg)
    # folder back + tab
    d.rounded_rectangle((170, 270, 500, 440), radius=60, fill=(224, 219, 255, 255))
    d.rounded_rectangle((170, 330, 854, 790), radius=76, fill=(224, 219, 255, 255))
    # sheet peeking out
    d.rounded_rectangle((230, 300, 794, 520), radius=40, fill=(255, 255, 255, 255))
    # front panel
    d.rounded_rectangle((170, 430, 854, 800), radius=76, fill=(255, 255, 255, 255))
    # sorted rows (descending lengths = "sorting")
    rows = [(230, 505, 700, (124, 108, 240)), (230, 590, 600, (157, 144, 247)),
            (230, 675, 490, (200, 192, 255))]
    for x0, y0, x1, col in rows:
        d.rounded_rectangle((x0, y0, x1, y0 + 44), radius=22, fill=col + (255,))
        d.ellipse((x1 + 28, y0 + 6, x1 + 60, y0 + 38), fill=col + (255,))

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(bg, (0, 0), rounded_mask(S, 230))
    return out


if __name__ == "__main__":
    img = build()
    img.resize((512, 512), Image.LANCZOS).save("danevo.png")
    img.save("danevo.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Created danevo.png and danevo.ico")
