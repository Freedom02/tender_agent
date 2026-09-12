from pathlib import Path

import numpy as np
from PIL import Image


INPUT = Path(r"C:\Users\乔远\AppData\Local\Temp\codex-clipboard-be40fc0a-3ebe-4e07-b399-f129b633c07c.png")
OUTPUT = Path(r"C:\Users\乔远\Desktop\tender_agent\preview")

PALETTES = {
    "tech-carbon-cyan": {
        "page": (8, 16, 28),
        "surface": (15, 27, 43),
        "panel": (19, 37, 57),
        "border": (39, 70, 91),
        "text": (220, 238, 249),
        "muted": (126, 158, 181),
        "primary": (38, 213, 255),
        "success": (41, 226, 181),
        "warning": (255, 185, 76),
        "danger": (255, 91, 116),
    },
    "tech-terminal-lime": {
        "page": (9, 17, 22),
        "surface": (16, 28, 34),
        "panel": (23, 41, 44),
        "border": (48, 77, 76),
        "text": (232, 244, 235),
        "muted": (139, 170, 151),
        "primary": (177, 239, 73),
        "success": (48, 226, 166),
        "warning": (255, 200, 85),
        "danger": (255, 100, 120),
    },
    "tech-cobalt-steel": {
        "page": (10, 15, 30),
        "surface": (22, 29, 49),
        "panel": (29, 39, 65),
        "border": (52, 66, 105),
        "text": (232, 237, 255),
        "muted": (139, 157, 198),
        "primary": (91, 140, 255),
        "success": (54, 214, 194),
        "warning": (255, 179, 71),
        "danger": (255, 107, 154),
    },
    "tech-industrial-orange": {
        "page": (20, 15, 13),
        "surface": (32, 25, 21),
        "panel": (47, 35, 28),
        "border": (87, 61, 44),
        "text": (250, 239, 226),
        "muted": (191, 157, 130),
        "primary": (255, 151, 61),
        "success": (53, 222, 190),
        "warning": (255, 193, 74),
        "danger": (255, 100, 89),
    },
}


def rgb(value):
    return np.asarray(value, dtype=np.float32)


def rgb_to_hsv(image):
    rgb_image = image / 255.0
    max_value = rgb_image.max(axis=2)
    min_value = rgb_image.min(axis=2)
    delta = max_value - min_value
    hue = np.zeros_like(max_value)

    nonzero = delta > 1e-6
    red = (max_value == rgb_image[..., 0]) & nonzero
    green = (max_value == rgb_image[..., 1]) & nonzero
    blue = (max_value == rgb_image[..., 2]) & nonzero
    hue[red] = ((rgb_image[..., 1] - rgb_image[..., 2])[red] / delta[red]) % 6
    hue[green] = ((rgb_image[..., 2] - rgb_image[..., 0])[green] / delta[green]) + 2
    hue[blue] = ((rgb_image[..., 0] - rgb_image[..., 1])[blue] / delta[blue]) + 4
    hue /= 6.0

    saturation = np.zeros_like(max_value)
    nonzero_max = max_value > 1e-6
    saturation[nonzero_max] = delta[nonzero_max] / max_value[nonzero_max]
    return hue, saturation, max_value


def colorize(source, palette):
    height = source.shape[0]
    output = source.copy()
    body = source[100:].astype(np.float32)
    hue, saturation, value = rgb_to_hsv(body)
    luminance = body @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32) / 255.0

    page = rgb(palette["page"])
    surface = rgb(palette["surface"])
    panel = rgb(palette["panel"])
    border = rgb(palette["border"])
    text = rgb(palette["text"])
    muted = rgb(palette["muted"])
    primary = rgb(palette["primary"])
    success = rgb(palette["success"])
    warning = rgb(palette["warning"])
    danger = rgb(palette["danger"])

    neutral = np.empty_like(body)
    neutral[:] = panel
    neutral[luminance > 0.965] = surface
    neutral[(luminance > 0.90) & (luminance <= 0.965)] = page
    neutral[(luminance > 0.66) & (luminance <= 0.90)] = border
    neutral[(luminance > 0.42) & (luminance <= 0.66)] = muted
    neutral[luminance <= 0.42] = text

    colored = np.empty_like(body)
    colored[:] = muted
    colored[luminance <= 0.42] = text
    colored[(luminance > 0.42) & (luminance <= 0.60)] = muted

    green = (hue >= 0.19) & (hue < 0.50) & (luminance > 0.60)
    warm = ((hue < 0.16) | (hue >= 0.93)) & (luminance > 0.60)
    cool = (hue >= 0.50) & (hue < 0.84) & (luminance > 0.60)
    colored[green] = success
    colored[warm] = warning
    colored[cool] = primary

    # Keep anti-aliased edges and low-saturation fills close to their original contrast.
    strength = np.clip(saturation * 1.35, 0.0, 1.0)[..., None]
    transformed = neutral * (1.0 - strength) + colored * strength

    # A small luminance correction keeps pale labels readable against the dark surfaces.
    source_level = np.clip(luminance[..., None], 0.0, 1.0)
    target_level = np.clip(transformed.mean(axis=2, keepdims=True) / 255.0, 0.03, 0.98)
    contrast = np.where(luminance[..., None] < 0.42, 1.04, 0.98)
    transformed = np.clip(transformed * contrast + (source_level - 0.5) * 8.0, 0, 255)

    output[100:] = transformed.astype(np.uint8)
    return output


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = np.asarray(Image.open(INPUT).convert("RGB"))
    for name, palette in PALETTES.items():
        image = Image.fromarray(colorize(source, palette), mode="RGB")
        image.save(OUTPUT / f"{name}.png")
        print(name)


if __name__ == "__main__":
    main()
