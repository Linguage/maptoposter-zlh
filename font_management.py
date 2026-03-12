"""Font management helpers for local Roboto files and optional Google Fonts downloads."""

import os
import re
from pathlib import Path

import requests

FONTS_DIR = "fonts"
FONTS_CACHE_DIR = Path(FONTS_DIR) / "cache"


def download_google_font(font_family, weights=None):
    """Download a Google Fonts family and cache its weight variants locally."""
    if weights is None:
        weights = [300, 400, 700]

    FONTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    font_name_safe = font_family.replace(" ", "_").lower()
    font_files = {}

    try:
        weights_str = ";".join(map(str, weights))
        response = requests.get(
            "https://fonts.googleapis.com/css2",
            params={"family": f"{font_family}:wght@{weights_str}"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        css_content = response.text

        weight_url_map = {}
        font_face_blocks = re.split(r"@font-face\s*\{", css_content)
        for block in font_face_blocks[1:]:
            weight_match = re.search(r"font-weight:\s*(\d+)", block)
            if not weight_match:
                continue

            url_match = re.search(r"url\((https://[^)]+\.(woff2|ttf))\)", block)
            if not url_match:
                continue

            weight_url_map[int(weight_match.group(1))] = url_match.group(1)

        weight_map = {300: "light", 400: "regular", 700: "bold"}
        for weight in weights:
            weight_key = weight_map.get(weight, "regular")
            weight_url = weight_url_map.get(weight)
            if not weight_url and weight_url_map:
                closest_weight = min(weight_url_map.keys(), key=lambda value: abs(value - weight))
                weight_url = weight_url_map[closest_weight]
                print(f"  Using weight {closest_weight} for {weight_key} (requested {weight} not available)")

            if not weight_url:
                continue

            file_ext = "woff2" if weight_url.endswith(".woff2") else "ttf"
            font_filename = f"{font_name_safe}_{weight_key}.{file_ext}"
            font_path = FONTS_CACHE_DIR / font_filename

            if not font_path.exists():
                print(f"  Downloading {font_family} {weight_key} ({weight})...")
                font_response = requests.get(weight_url, timeout=10)
                font_response.raise_for_status()
                font_path.write_bytes(font_response.content)
            else:
                print(f"  Using cached {font_family} {weight_key}")

            font_files[weight_key] = str(font_path)

        if "regular" not in font_files and font_files:
            first_key = next(iter(font_files))
            font_files["regular"] = font_files[first_key]
            print(f"  Using {first_key} weight as regular")

        if "bold" not in font_files and "regular" in font_files:
            font_files["bold"] = font_files["regular"]
            print("  Using regular weight as bold")
        if "light" not in font_files and "regular" in font_files:
            font_files["light"] = font_files["regular"]
            print("  Using regular weight as light")

        return font_files if font_files else None
    except Exception as exc:
        print(f"⚠ Error downloading Google Font '{font_family}': {exc}")
        return None


def load_fonts(font_family=None):
    """Load local Roboto fonts or download the requested Google Fonts family."""
    if font_family and font_family.lower() != "roboto":
        print(f"Loading Google Font: {font_family}")
        fonts = download_google_font(font_family)
        if fonts:
            print(f"✓ Font '{font_family}' loaded successfully")
            return fonts
        print(f"⚠ Failed to load '{font_family}', falling back to local Roboto")

    fonts = {
        "bold": os.path.join(FONTS_DIR, "Roboto-Bold.ttf"),
        "regular": os.path.join(FONTS_DIR, "Roboto-Regular.ttf"),
        "light": os.path.join(FONTS_DIR, "Roboto-Light.ttf"),
    }

    for path in fonts.values():
        if not os.path.exists(path):
            print(f"⚠ Font not found: {path}")
            return None

    return fonts