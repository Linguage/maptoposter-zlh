import argparse
import asyncio
import hashlib
import json
import math
import os
import pickle
import sys
import time
from datetime import datetime

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
from geopy.geocoders import Nominatim
from matplotlib.font_manager import FontProperties
from tqdm import tqdm

from font_management import load_fonts

THEMES_DIR = "themes"
DEFAULT_OUTPUT_DIR = "generated_posters"
CACHE_DIR = os.environ.get("CACHE_DIR", "cache")
FILE_ENCODING = "utf-8"


class CacheError(Exception):
    """Raised when cache operations fail."""


def ensure_directory(path):
    """Create a directory if it does not already exist."""
    os.makedirs(path, exist_ok=True)


ensure_directory(CACHE_DIR)
FONTS = load_fonts()


def _cache_path(key):
    """Return a stable cache file path for the given key."""
    digest = hashlib.sha1(key.encode(FILE_ENCODING)).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.pkl")


def cache_get(key):
    """Read a cached object if available."""
    path = _cache_path(key)
    if not os.path.exists(path):
        return None

    try:
        with open(path, "rb") as handle:
            return pickle.load(handle)
    except Exception as exc:
        raise CacheError(f"Cache read failed: {exc}") from exc


def cache_set(key, value):
    """Persist an object to the local cache."""
    ensure_directory(CACHE_DIR)
    path = _cache_path(key)
    try:
        with open(path, "wb") as handle:
            pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        raise CacheError(f"Cache write failed: {exc}") from exc


def is_latin_script(text):
    """Return whether the text is mostly Latin script."""
    if not text:
        return True

    latin_count = 0
    total_alpha = 0
    for char in text:
        if char.isalpha():
            total_alpha += 1
            if ord(char) < 0x250:
                latin_count += 1

    if total_alpha == 0:
        return True

    return (latin_count / total_alpha) > 0.8


def generate_output_filename(city, theme_name, output_dir=DEFAULT_OUTPUT_DIR, output_format="png"):
    """Generate a unique output filename using city, theme, and timestamp."""
    ensure_directory(output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(" ", "_")
    filename = f"{city_slug}_{theme_name}_{timestamp}.{output_format.lower()}"
    return os.path.join(output_dir, filename)


def get_available_themes():
    """Return theme names from the themes directory."""
    ensure_directory(THEMES_DIR)
    return [file[:-5] for file in sorted(os.listdir(THEMES_DIR)) if file.endswith(".json")]


def load_theme(theme_name="feature_based"):
    """Load a theme JSON file or fall back to the local default theme."""
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")
    if not os.path.exists(theme_file):
        print(f"⚠ Theme file '{theme_file}' not found. Using default feature_based theme.")
        return {
            "name": "Feature-Based Shading",
            "bg": "#FFFFFF",
            "text": "#000000",
            "gradient_color": "#FFFFFF",
            "water": "#C0C0C0",
            "parks": "#F0F0F0",
            "road_motorway": "#0A0A0A",
            "road_primary": "#1A1A1A",
            "road_secondary": "#2A2A2A",
            "road_tertiary": "#3A3A3A",
            "road_residential": "#4A4A4A",
            "road_default": "#3A3A3A",
        }

    with open(theme_file, "r", encoding=FILE_ENCODING) as handle:
        theme = json.load(handle)
    print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
    if "description" in theme:
        print(f"  {theme['description']}")
    return theme


THEME = {}


def create_gradient_fade(ax, color, location="bottom", zorder=10):
    """Create a vertical fade overlay at the top or bottom of the map."""
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))

    rgb = mcolors.to_rgb(color)
    colors = np.zeros((256, 4))
    colors[:, 0] = rgb[0]
    colors[:, 1] = rgb[1]
    colors[:, 2] = rgb[2]

    if location == "bottom":
        colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = 0.25
    else:
        colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 0.75
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(colors)
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]
    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end

    ax.imshow(
        gradient,
        extent=[xlim[0], xlim[1], y_bottom, y_top],
        aspect="auto",
        cmap=custom_cmap,
        zorder=zorder,
        origin="lower",
    )


def get_edge_colors_by_type(graph):
    """Return road colors according to OSM highway hierarchy."""
    edge_colors = []
    for _, _, data in graph.edges(data=True):
        highway = data.get("highway", "unclassified")
        if isinstance(highway, list):
            highway = highway[0] if highway else "unclassified"

        if highway in ["motorway", "motorway_link"]:
            color = THEME["road_motorway"]
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            color = THEME["road_primary"]
        elif highway in ["secondary", "secondary_link"]:
            color = THEME["road_secondary"]
        elif highway in ["tertiary", "tertiary_link"]:
            color = THEME["road_tertiary"]
        elif highway in ["residential", "living_street", "unclassified"]:
            color = THEME["road_residential"]
        else:
            color = THEME["road_default"]
        edge_colors.append(color)
    return edge_colors


def get_edge_widths_by_type(graph):
    """Return line widths according to OSM highway hierarchy."""
    edge_widths = []
    for _, _, data in graph.edges(data=True):
        highway = data.get("highway", "unclassified")
        if isinstance(highway, list):
            highway = highway[0] if highway else "unclassified"

        if highway in ["motorway", "motorway_link"]:
            width = 1.2
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            width = 1.0
        elif highway in ["secondary", "secondary_link"]:
            width = 0.8
        elif highway in ["tertiary", "tertiary_link"]:
            width = 0.6
        else:
            width = 0.4
        edge_widths.append(width)
    return edge_widths


def resolve_geocode_result(location):
    """Resolve geopy coroutine results in environments that return awaitables."""
    if not asyncio.iscoroutine(location):
        return location

    try:
        return asyncio.run(location)
    except RuntimeError as exc:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError(
                "Geocoder returned a coroutine while an event loop is already running. "
                "Run this script in a synchronous environment."
            ) from exc
        return loop.run_until_complete(location)


def get_coordinates(city, country, landmark=None, custom_coords=None):
    """Resolve map center coordinates using custom input, landmark search, or city geocoding."""
    if custom_coords:
        try:
            lat, lon = map(float, custom_coords.split(","))
            print(f"✓ Using custom coordinates: {lat}, {lon}")
            return (lat, lon)
        except ValueError as exc:
            raise ValueError("Invalid custom coordinates format. Use 'lat,lon' format.") from exc

    cache_key = json.dumps(
        {
            "type": "coords",
            "city": city.lower(),
            "country": country.lower(),
            "landmark": (landmark or "").lower(),
        },
        sort_keys=True,
    )
    cached = cache_get(cache_key)
    if cached:
        print(f"✓ Using cached coordinates for {city}, {country}")
        return cached

    print("Looking up coordinates...")
    geolocator = Nominatim(user_agent="city_map_poster", timeout=10)
    time.sleep(1)

    if landmark:
        query = f"{landmark}, {city}, {country}"
        location = resolve_geocode_result(geolocator.geocode(query, timeout=10))
        if location:
            coords = (location.latitude, location.longitude)
            print(f"✓ Found landmark: {location.address}")
            print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
            try:
                cache_set(cache_key, coords)
            except CacheError as exc:
                print(exc)
            return coords
        print(f"⚠ Landmark '{landmark}' not found, falling back to city center")

    locations = resolve_geocode_result(
        geolocator.geocode(f"{city}, {country}", exactly_one=False, limit=5, timeout=10)
    )
    if not locations:
        raise ValueError(f"Could not find coordinates for {city}, {country}")

    selected = None
    for location in locations:
        addr_type = location.raw.get("addresstype", "")
        if addr_type in ["city", "suburb", "town", "village"]:
            selected = location
            break
    if selected is None:
        selected = locations[0]

    coords = (selected.latitude, selected.longitude)
    print(f"✓ Found: {selected.address}")
    print(f"✓ Coordinates: {selected.latitude}, {selected.longitude}")
    print(f"  (Type: {selected.raw.get('addresstype', 'unknown')})")
    try:
        cache_set(cache_key, coords)
    except CacheError as exc:
        print(exc)
    return coords


def calculate_bbox(point, dist, aspect_ratio, stretch=False):
    """Return bbox and coverage distances derived from the requested poster ratio."""
    if stretch:
        dist_horiz = dist
        dist_vert = dist
    else:
        if aspect_ratio > 1:
            dist_horiz = dist
            dist_vert = dist / aspect_ratio
        else:
            dist_horiz = dist * aspect_ratio
            dist_vert = dist

    lat, lon = point
    m_per_deg_lat = 111000
    m_per_deg_lon = 111000 * max(0.1, abs(math.cos(math.radians(lat))))
    lat_extent = dist_vert / m_per_deg_lat
    lon_extent = dist_horiz / m_per_deg_lon
    north = lat + lat_extent / 2
    south = lat - lat_extent / 2
    east = lon + lon_extent / 2
    west = lon - lon_extent / 2
    return (west, south, east, north), dist_horiz, dist_vert


def fetch_graph(bbox):
    """Fetch and cache the street graph for a bounding box."""
    cache_key = json.dumps({"type": "graph", "bbox": [round(value, 6) for value in bbox]}, sort_keys=True)
    cached = cache_get(cache_key)
    if cached is not None:
        print("✓ Using cached street network")
        return cached

    graph = ox.graph_from_bbox(bbox, network_type="all")
    time.sleep(0.5)
    try:
        cache_set(cache_key, graph)
    except CacheError as exc:
        print(exc)
    return graph


def fetch_features(bbox, tags, name):
    """Fetch and cache polygon features for a bounding box."""
    cache_key = json.dumps(
        {"type": name, "bbox": [round(value, 6) for value in bbox], "tags": tags},
        sort_keys=True,
    )
    cached = cache_get(cache_key)
    if cached is not None:
        print(f"✓ Using cached {name}")
        return cached

    try:
        data = ox.features_from_bbox(bbox, tags=tags)
        time.sleep(0.3)
        try:
            cache_set(cache_key, data)
        except CacheError as exc:
            print(exc)
        return data
    except Exception:
        return None


def plot_area_features(features, ax, color, zorder):
    """Plot only polygonal features to avoid stray point markers."""
    if features is None or features.empty:
        return

    area_features = features[features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if not area_features.empty:
        area_features.plot(ax=ax, facecolor=color, edgecolor="none", zorder=zorder)


def build_fonts(figsize, fonts, city_text):
    """Build typography objects with light scaling and long-name handling."""
    active_fonts = fonts or FONTS
    scale_factor = min(figsize[0], figsize[1]) / 12.0
    main_size = 60 * scale_factor
    if len(city_text) > 12:
        main_size = max(34 * scale_factor, main_size - (len(city_text) - 12) * 2)

    if active_fonts:
        return {
            "main": FontProperties(fname=active_fonts["bold"], size=main_size),
            "sub": FontProperties(fname=active_fonts["light"], size=22 * scale_factor),
            "coords": FontProperties(fname=active_fonts["regular"], size=14 * scale_factor),
            "attr": FontProperties(fname=active_fonts["light"], size=8 * scale_factor),
        }

    return {
        "main": FontProperties(family="monospace", weight="bold", size=main_size),
        "sub": FontProperties(family="monospace", weight="normal", size=22 * scale_factor),
        "coords": FontProperties(family="monospace", size=14 * scale_factor),
        "attr": FontProperties(family="monospace", size=8 * scale_factor),
    }


def create_poster(
    city,
    country,
    point,
    dist,
    output_file,
    ratio="1:1",
    stretch=False,
    display_city=None,
    display_country=None,
    fonts=None,
    output_format="png",
):
    """Generate and save a map poster with optional formatting and font overrides."""
    print(f"\nGenerating map for {city}, {country}...")

    try:
        width, height = map(float, ratio.split(":"))
        scale = 12 / max(width, height)
        figsize = (width * scale, height * scale)
    except ValueError:
        figsize = (12, 12)

    aspect_ratio = figsize[0] / figsize[1]
    bbox, dist_horiz, dist_vert = calculate_bbox(point, dist, aspect_ratio, stretch)

    mode = "STRETCH" if stretch else "CROP"
    print(f"  Mode: {mode} ({ratio})")
    print(f"  Canvas: {figsize[0]:.0f}x{figsize[1]:.0f} (aspect: {aspect_ratio:.2f})")
    print(f"  Coverage: {dist_horiz:.0f}m horizontal x {dist_vert:.0f}m vertical")
    print(f"  BBox: N={bbox[3]:.4f}, S={bbox[1]:.4f}, E={bbox[2]:.4f}, W={bbox[0]:.4f}")

    with tqdm(
        total=3,
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
    ) as progress:
        progress.set_description("Downloading street network")
        graph = fetch_graph(bbox)
        progress.update(1)

        progress.set_description("Downloading water features")
        water = fetch_features(
            bbox,
            {"natural": ["water", "bay", "strait"], "water": True, "waterway": "riverbank"},
            "water",
        )
        progress.update(1)

        progress.set_description("Downloading parks/green spaces")
        parks = fetch_features(bbox, {"leisure": "park", "landuse": "grass"}, "parks")
        progress.update(1)

    print("✓ All data downloaded successfully!")
    print("Rendering map...")

    fig, ax = plt.subplots(figsize=figsize, facecolor=THEME["bg"])
    ax.set_facecolor(THEME["bg"])
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal")

    plot_area_features(water, ax, THEME["water"], 1)
    plot_area_features(parks, ax, THEME["parks"], 2)

    print("Applying road hierarchy colors...")
    edge_colors = get_edge_colors_by_type(graph)
    edge_widths = get_edge_widths_by_type(graph)
    ox.plot_graph(
        graph,
        ax=ax,
        bgcolor=THEME["bg"],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False,
        close=False,
    )

    create_gradient_fade(ax, THEME["gradient_color"], location="bottom", zorder=10)
    create_gradient_fade(ax, THEME["gradient_color"], location="top", zorder=10)

    city_label = display_city or city
    country_label = display_country or country
    city_text = city_label.upper() if is_latin_script(city_label) else city_label
    country_text = country_label.upper() if is_latin_script(country_label) else country_label
    spaced_city = "  ".join(list(city_text)) if is_latin_script(city_label) else city_text
    font_set = build_fonts(figsize, fonts, city_text)

    height_factor = figsize[1] / 12.0
    pos_factor = 1 / height_factor if height_factor < 1 else 1.0
    city_y = 0.14 * pos_factor
    country_y = 0.10 * pos_factor
    coords_y = 0.07 * pos_factor
    line_y = 0.125 * pos_factor

    ax.text(
        0.5,
        city_y,
        spaced_city,
        transform=ax.transAxes,
        color=THEME["text"],
        ha="center",
        fontproperties=font_set["main"],
        zorder=11,
    )
    ax.text(
        0.5,
        country_y,
        country_text,
        transform=ax.transAxes,
        color=THEME["text"],
        ha="center",
        fontproperties=font_set["sub"],
        zorder=11,
    )

    lat, lon = point
    coords_text = f"{lat:.4f}° N / {lon:.4f}° E" if lat >= 0 else f"{abs(lat):.4f}° S / {lon:.4f}° E"
    if lon < 0:
        coords_text = coords_text.replace("E", "W")

    ax.text(
        0.5,
        coords_y,
        coords_text,
        transform=ax.transAxes,
        color=THEME["text"],
        alpha=0.7,
        ha="center",
        fontproperties=font_set["coords"],
        zorder=11,
    )
    ax.plot([0.4, 0.6], [line_y, line_y], transform=ax.transAxes, color=THEME["text"], linewidth=1, zorder=11)
    ax.text(
        0.98,
        0.02,
        "© OpenStreetMap contributors",
        transform=ax.transAxes,
        color=THEME["text"],
        alpha=0.5,
        ha="right",
        va="bottom",
        fontproperties=font_set["attr"],
        zorder=11,
    )

    print(f"Saving to {output_file}...")
    save_kwargs = {"facecolor": THEME["bg"], "format": output_format.lower()}
    if output_format.lower() == "png":
        save_kwargs["dpi"] = 300
    plt.savefig(output_file, **save_kwargs)
    plt.close()
    print(f"✓ Done! Poster saved as {output_file}")


def print_examples():
    """Print command-line usage examples."""
    print(
        """
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000
  python create_map_poster.py -c "Chengdu" -C "China" -t midnight_blue -r 16:9
  python create_map_poster.py -c "Beijing" -C "China" -t noir -l "Tiananmen Square"
  python create_map_poster.py -c "Tokyo" -C "Japan" --display-city "東京" --font-family "Noto Sans JP"
  python create_map_poster.py -c "Paris" -C "France" --all-themes -d 10000
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint --format svg
  python create_map_poster.py --list-themes

Options:
  --city, -c           City name (required)
  --country, -C        Country name (required)
  --theme, -t          Theme name (default: feature_based)
  --all-themes         Generate posters for all themes
  --distance, -d       Map radius in meters (default: 29000)
  --ratio, -r          Aspect ratio as width:height (default: 1:1)
  --stretch, -s        Stretch mode instead of crop mode
  --landmark, -l       Landmark name to center on
  --coords             Custom coordinates as lat,lon
  --output-dir, -o     Output directory (default: generated_posters)
  --display-city       Override displayed city name
  --display-country    Override displayed country name
  --font-family        Google Fonts family name
  --format, -f         Output format: png, svg, pdf
  --list-themes        List all available themes

Generated posters are saved to 'generated_posters/' by default.
"""
    )


def list_themes():
    """List all available themes with names and descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, "r", encoding=FILE_ENCODING) as handle:
                theme_data = json.load(handle)
                display_name = theme_data.get("name", theme_name)
                description = theme_data.get("description", "")
        except Exception:
            display_name = theme_name
            description = ""

        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city Tokyo --country Japan --theme midnight_blue
  python create_map_poster.py --city Paris --country France --theme noir --distance 15000
  python create_map_poster.py --city Tokyo --country Japan --display-city "東京" --font-family "Noto Sans JP"
  python create_map_poster.py --list-themes
        """,
    )

    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="feature_based",
        help="Theme name (default: feature_based)",
    )
    parser.add_argument(
        "--all-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument("--distance", "-d", type=int, default=29000, help="Map radius in meters (default: 29000)")
    parser.add_argument(
        "--ratio",
        "-r",
        type=str,
        default="1:1",
        help="Aspect ratio as width:height (default: 1:1, e.g., 16:9, 16:12, 12:16)",
    )
    parser.add_argument(
        "--stretch",
        "-s",
        action="store_true",
        help="Stretch mode: download 1:1 data then stretch to target ratio",
    )
    parser.add_argument(
        "--landmark",
        "-l",
        type=str,
        help='Landmark name to center the map on (e.g., "Forbidden City", "Tiananmen Square")',
    )
    parser.add_argument("--coords", type=str, help='Custom center coordinates as "lat,lon" (e.g., "39.91,116.40")')
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated posters (default: generated_posters)",
    )
    parser.add_argument("--display-city", type=str, help="Custom display name for the city label")
    parser.add_argument("--display-country", type=str, help="Custom display name for the country label")
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (e.g., "Noto Sans JP", "Open Sans")',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )
    parser.add_argument("--list-themes", action="store_true", help="List all available themes")

    args = parser.parse_args()

    if len(sys.argv) == 1:
        print_examples()
        sys.exit(0)

    if args.list_themes:
        list_themes()
        sys.exit(0)

    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        sys.exit(1)

    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        sys.exit(1)

    if args.all_themes:
        themes_to_generate = available_themes
    else:
        if args.theme not in available_themes:
            print(f"Error: Theme '{args.theme}' not found.")
            print(f"Available themes: {', '.join(available_themes)}")
            sys.exit(1)
        themes_to_generate = [args.theme]

    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)

    custom_fonts = None
    if args.font_family:
        custom_fonts = load_fonts(args.font_family)
        if not custom_fonts:
            print(f"⚠ Failed to load '{args.font_family}', falling back to Roboto")

    try:
        coords = get_coordinates(args.city, args.country, args.landmark, args.coords)
        for theme_name in themes_to_generate:
            THEME = load_theme(theme_name)
            output_file = generate_output_filename(args.city, theme_name, args.output_dir, args.format)
            create_poster(
                args.city,
                args.country,
                coords,
                args.distance,
                output_file,
                args.ratio,
                args.stretch,
                display_city=args.display_city,
                display_country=args.display_country,
                fonts=custom_fonts,
                output_format=args.format,
            )

        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)
    except Exception as exc:
        print(f"\n✗ Error: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
