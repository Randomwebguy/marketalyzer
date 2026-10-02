"""Draw the app icons into marketalyzer/web/static/icons (needs Pillow)."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "marketalyzer" / "web" / "static" / "icons"
BACKGROUND = (22, 22, 22)
PANEL = (32, 33, 36)
ACCENT = (57, 135, 229)
# A rising price line, in fractions of the drawing area.
LINE = [(0.0, 0.78), (0.22, 0.6), (0.4, 0.68), (0.62, 0.36), (0.8, 0.44), (1.0, 0.16)]


def draw(size: int, padding: float, rounded: bool) -> Image.Image:
    """Draw one icon; ``rounded`` gives it transparent rounded corners."""
    scale = 4  # supersample, then shrink, for smooth edges
    big = size * scale
    image = Image.new("RGB", (big, big), BACKGROUND)
    pen = ImageDraw.Draw(image)
    if rounded:
        image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        pen = ImageDraw.Draw(image)
        pen.rounded_rectangle(
            (0, 0, big - 1, big - 1), radius=big * 0.22, fill=BACKGROUND
        )
    inset = big * padding
    pen.rounded_rectangle(
        (inset, inset, big - inset, big - inset), radius=big * 0.14, fill=PANEL
    )
    area = (
        inset + big * 0.1,
        inset + big * 0.12,
        big - inset - big * 0.1,
        big - inset - big * 0.12,
    )
    width, height = area[2] - area[0], area[3] - area[1]
    points = [(area[0] + x * width, area[1] + y * height) for x, y in LINE]
    pen.line(points, fill=ACCENT, width=int(big * 0.055), joint="curve")
    x, y = points[-1]
    radius = big * 0.05
    pen.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255))
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    """Write every icon size the web app uses."""
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.1, rounded=True).save(OUT / "icon-192.png")
    draw(512, 0.1, rounded=True).save(OUT / "icon-512.png")
    # Maskable icons fill the square; the platform crops them, so keep the
    # drawing inside the central safe zone.
    draw(512, 0.2, rounded=False).save(OUT / "icon-maskable-512.png")
    draw(180, 0.12, rounded=False).save(OUT / "apple-touch-icon.png")


if __name__ == "__main__":
    main()
