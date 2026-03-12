import osmnx as ox
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import matplotlib.colors as mcolors
import numpy as np
from geopy.geocoders import Nominatim
from tqdm import tqdm
import time
import json
import os
import math
from datetime import datetime
import argparse

THEMES_DIR = "themes"
FONTS_DIR = "fonts"
DEFAULT_OUTPUT_DIR = "generated_posters"

def load_fonts():
    """
    Load Roboto fonts from the fonts directory.
    Returns dict with font paths for different weights.
    """
    fonts = {
        'bold': os.path.join(FONTS_DIR, 'Roboto-Bold.ttf'),
        'regular': os.path.join(FONTS_DIR, 'Roboto-Regular.ttf'),
        'light': os.path.join(FONTS_DIR, 'Roboto-Light.ttf')
    }
    
    # Verify fonts exist
    for weight, path in fonts.items():
        if not os.path.exists(path):
            print(f"⚠ Font not found: {path}")
            return None
    
    return fonts

FONTS = load_fonts()

def generate_output_filename(city, theme_name, output_dir=DEFAULT_OUTPUT_DIR):
    """
    Generate unique output filename with city, theme, and datetime.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(' ', '_')
    filename = f"{city_slug}_{theme_name}_{timestamp}.png"
    return os.path.join(output_dir, filename)

def get_available_themes():
    """
    Scans the themes directory and returns a list of available theme names.
    """
    if not os.path.exists(THEMES_DIR):
        os.makedirs(THEMES_DIR)
        return []
    
    themes = []
    for file in sorted(os.listdir(THEMES_DIR)):
        if file.endswith('.json'):
            theme_name = file[:-5]  # Remove .json extension
            themes.append(theme_name)
    return themes

def load_theme(theme_name="feature_based"):
    """
    Load theme from JSON file in themes directory.
    """
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")
    
    if not os.path.exists(theme_file):
        print(f"⚠ Theme file '{theme_file}' not found. Using default feature_based theme.")
        # Fallback to embedded default theme
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
            "road_default": "#3A3A3A"
        }
    
    with open(theme_file, 'r') as f:
        theme = json.load(f)
        print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
        if 'description' in theme:
            print(f"  {theme['description']}")
        return theme

# Load theme (can be changed via command line or input)
THEME = None  # Will be loaded later

def create_gradient_fade(ax, color, location='bottom', zorder=10):
    """
    Creates a fade effect at the top or bottom of the map.
    """
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))
    
    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]
    
    if location == 'bottom':
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = 0.25
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 0.75
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(my_colors)
    
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]
    
    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end
    
    ax.imshow(gradient, extent=[xlim[0], xlim[1], y_bottom, y_top], 
              aspect='auto', cmap=custom_cmap, zorder=zorder, origin='lower')

def get_edge_colors_by_type(G):
    """
    Assigns colors to edges based on road type hierarchy.
    Returns a list of colors corresponding to each edge in the graph.
    """
    edge_colors = []
    
    for u, v, data in G.edges(data=True):
        # Get the highway type (can be a list or string)
        highway = data.get('highway', 'unclassified')
        
        # Handle list of highway types (take the first one)
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'
        
        # Assign color based on road type
        if highway in ['motorway', 'motorway_link']:
            color = THEME['road_motorway']
        elif highway in ['trunk', 'trunk_link', 'primary', 'primary_link']:
            color = THEME['road_primary']
        elif highway in ['secondary', 'secondary_link']:
            color = THEME['road_secondary']
        elif highway in ['tertiary', 'tertiary_link']:
            color = THEME['road_tertiary']
        elif highway in ['residential', 'living_street', 'unclassified']:
            color = THEME['road_residential']
        else:
            color = THEME['road_default']
        
        edge_colors.append(color)
    
    return edge_colors

def get_edge_widths_by_type(G):
    """
    Assigns line widths to edges based on road type.
    Major roads get thicker lines.
    """
    edge_widths = []
    
    for u, v, data in G.edges(data=True):
        highway = data.get('highway', 'unclassified')
        
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'
        
        # Assign width based on road importance
        if highway in ['motorway', 'motorway_link']:
            width = 1.2
        elif highway in ['trunk', 'trunk_link', 'primary', 'primary_link']:
            width = 1.0
        elif highway in ['secondary', 'secondary_link']:
            width = 0.8
        elif highway in ['tertiary', 'tertiary_link']:
            width = 0.6
        else:
            width = 0.4
        
        edge_widths.append(width)
    
    return edge_widths

def get_coordinates(city, country, landmark=None, custom_coords=None):
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.

    Args:
        city: City name
        country: Country name
        landmark: Optional landmark name to search within the city
        custom_coords: Optional custom coordinates as "lat,lon" string
    """
    print("Looking up coordinates...")
    geolocator = Nominatim(user_agent="city_map_poster")

    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)

    # If custom coordinates provided, use them directly
    if custom_coords:
        try:
            lat, lon = map(float, custom_coords.split(','))
            print(f"✓ Using custom coordinates: {lat}, {lon}")
            return (lat, lon)
        except:
            raise ValueError(f"Invalid custom coordinates format. Use 'lat,lon' format.")

    # If landmark provided, search for it
    if landmark:
        query = f"{landmark}, {city}, {country}"
        location = geolocator.geocode(query, timeout=10)
        if location:
            print(f"✓ Found landmark: {location.address}")
            print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
            return (location.latitude, location.longitude)
        else:
            print(f"⚠ Landmark '{landmark}' not found, falling back to city center")

    # Get multiple candidates and find the best city-level result
    locations = geolocator.geocode(f"{city}, {country}", exactly_one=False, limit=5, timeout=10)

    if not locations:
        raise ValueError(f"Could not find coordinates for {city}, {country}")

    # Prefer city or suburb level results over state/administrative
    location = None
    for loc in locations:
        addr_type = loc.raw.get('addresstype', '')
        # Prefer city, suburb, or town level results
        if addr_type in ['city', 'suburb', 'town', 'village']:
            location = loc
            break

    # Fall back to first result if no city-level found
    if location is None:
        location = locations[0]

    print(f"✓ Found: {location.address}")
    print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
    print(f"  (Type: {location.raw.get('addresstype', 'unknown')})")

    return (location.latitude, location.longitude)

def create_poster(city, country, point, dist, output_file, ratio='1:1', stretch=False):
    print(f"\nGenerating map for {city}, {country}...")

    # Parse ratio (format: "width:height")
    try:
        width, height = map(float, ratio.split(':'))
        # Scale to reasonable figure size (base unit = 12)
        scale = 12 / max(width, height)
        figsize = (width * scale, height * scale)
    except:
        figsize = (12, 12)  # Default fallback

    aspect_ratio = figsize[0] / figsize[1]

    if stretch:
        # Stretch mode: download 1:1 data, then stretch to target ratio
        dist_horiz = dist
        dist_vert = dist
        print(f"  Mode: STRETCH (download 1:1, then stretch to {ratio})")
    else:
        # Crop mode: download data matching the target aspect ratio
        if aspect_ratio > 1:
            dist_horiz = dist
            dist_vert = dist / aspect_ratio
        else:
            dist_horiz = dist * aspect_ratio
            dist_vert = dist
        print(f"  Mode: CROP (download data matching {ratio} ratio)")

    print(f"  Canvas: {figsize[0]:.0f}x{figsize[1]:.0f} (aspect: {aspect_ratio:.2f})")
    print(f"  Coverage: {dist_horiz:.0f}m horizontal x {dist_vert:.0f}m vertical")

    # Calculate geographic bounding box based on aspect ratio
    lat, lon = point
    m_per_deg_lat = 111000
    m_per_deg_lon = 111000 * max(0.1, abs(math.cos(math.radians(lat))))

    lat_extent = dist_vert / m_per_deg_lat
    lon_extent = dist_horiz / m_per_deg_lon

    # Calculate bbox coordinates
    north = lat + lat_extent / 2
    south = lat - lat_extent / 2
    east = lon + lon_extent / 2
    west = lon - lon_extent / 2

    print(f"  BBox: N={north:.4f}, S={south:.4f}, E={east:.4f}, W={west:.4f}")

    # Create bbox tuple in OSMnx format: (west, south, east, north)
    bbox = (west, south, east, north)

    # Progress bar for data fetching
    with tqdm(total=3, desc="Fetching map data", unit="step", bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}') as pbar:
        # 1. Fetch Street Network using bbox
        pbar.set_description("Downloading street network")
        G = ox.graph_from_bbox(bbox, network_type='all')
        pbar.update(1)
        time.sleep(0.5)  # Rate limit between requests

        # 2. Fetch Water Features using bbox
        pbar.set_description("Downloading water features")
        try:
            water = ox.features_from_bbox(bbox,
                                         tags={'natural': 'water', 'waterway': 'riverbank'})
        except:
            water = None
        pbar.update(1)
        time.sleep(0.3)

        # 3. Fetch Parks using bbox
        pbar.set_description("Downloading parks/green spaces")
        try:
            parks = ox.features_from_bbox(bbox,
                                         tags={'leisure': 'park', 'landuse': 'grass'})
        except:
            parks = None
        pbar.update(1)
    
    print("✓ All data downloaded successfully!")

    # 2. Setup Plot
    print("Rendering map...")
    fig, ax = plt.subplots(figsize=figsize, facecolor=THEME['bg'])
    ax.set_facecolor(THEME['bg'])
    ax.set_position([0, 0, 1, 1])

    # Set axis limits using the calculated bounding box
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_aspect('equal')  # This ensures the map doesn't get distorted
    
    # 3. Plot Layers
    # Layer 1: Polygons
    if water is not None and not water.empty:
        water.plot(ax=ax, facecolor=THEME['water'], edgecolor='none', zorder=1)
    if parks is not None and not parks.empty:
        parks.plot(ax=ax, facecolor=THEME['parks'], edgecolor='none', zorder=2)
    
    # Layer 2: Roads with hierarchy coloring
    print("Applying road hierarchy colors...")
    edge_colors = get_edge_colors_by_type(G)
    edge_widths = get_edge_widths_by_type(G)
    
    ox.plot_graph(
        G, ax=ax, bgcolor=THEME['bg'],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False, close=False
    )
    
    # Layer 3: Gradients (Top and Bottom)
    create_gradient_fade(ax, THEME['gradient_color'], location='bottom', zorder=10)
    create_gradient_fade(ax, THEME['gradient_color'], location='top', zorder=10)
    
    # 4. Typography using Roboto font
    if FONTS:
        font_main = FontProperties(fname=FONTS['bold'], size=60)
        font_top = FontProperties(fname=FONTS['bold'], size=40)
        font_sub = FontProperties(fname=FONTS['light'], size=22)
        font_coords = FontProperties(fname=FONTS['regular'], size=14)
    else:
        # Fallback to system fonts
        font_main = FontProperties(family='monospace', weight='bold', size=60)
        font_top = FontProperties(family='monospace', weight='bold', size=40)
        font_sub = FontProperties(family='monospace', weight='normal', size=22)
        font_coords = FontProperties(family='monospace', size=14)
    
    spaced_city = "  ".join(list(city.upper()))

    # Dynamically adjust text positions based on figure height
    # For 1:1 (12x12), use base positions; scale for other aspect ratios
    height_factor = figsize[1] / 12.0

    # Scale text positions inversely with height
    # Lower height (wider aspect ratio) = higher position values to spread out content
    if height_factor < 1:
        # When height is less than 12, spread content further apart
        pos_factor = 1 / height_factor
    else:
        # When height is equal or greater, use standard spacing
        pos_factor = 1.0

    # Base positions for 1:1 ratio
    base_city_y = 0.14
    base_country_y = 0.10
    base_coords_y = 0.07
    base_line_y = 0.125

    # Adjust positions based on aspect ratio
    city_y = base_city_y * pos_factor
    country_y = base_country_y * pos_factor
    coords_y = base_coords_y * pos_factor
    line_y = base_line_y * pos_factor

    # --- BOTTOM TEXT ---
    ax.text(0.5, city_y, spaced_city, transform=ax.transAxes,
            color=THEME['text'], ha='center', fontproperties=font_main, zorder=11)

    ax.text(0.5, country_y, country.upper(), transform=ax.transAxes,
            color=THEME['text'], ha='center', fontproperties=font_sub, zorder=11)

    lat, lon = point
    coords = f"{lat:.4f}° N / {lon:.4f}° E" if lat >= 0 else f"{abs(lat):.4f}° S / {lon:.4f}° E"
    if lon < 0:
        coords = coords.replace("E", "W")

    ax.text(0.5, coords_y, coords, transform=ax.transAxes,
            color=THEME['text'], alpha=0.7, ha='center', fontproperties=font_coords, zorder=11)

    ax.plot([0.4, 0.6], [line_y, line_y], transform=ax.transAxes,
            color=THEME['text'], linewidth=1, zorder=11)

    # --- ATTRIBUTION (bottom right) ---
    if FONTS:
        font_attr = FontProperties(fname=FONTS['light'], size=8)
    else:
        font_attr = FontProperties(family='monospace', size=8)
    
    ax.text(0.98, 0.02, "© OpenStreetMap contributors", transform=ax.transAxes,
            color=THEME['text'], alpha=0.5, ha='right', va='bottom', 
            fontproperties=font_attr, zorder=11)

    # 5. Save
    print(f"Saving to {output_file}...")
    plt.savefig(output_file, dpi=300, facecolor=THEME['bg'])
    plt.close()
    print(f"✓ Done! Poster saved as {output_file}")

def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid
  
  # Waterfront & canals
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline
  
  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads
  
  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout
  
  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula
  
  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split
  
  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --theme, -t       Theme name (default: feature_based)
  --distance, -d    Map radius in meters (default: 29000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes can be found in the 'themes/' directory.
Generated posters are saved to 'posters/' directory.
""")

def list_themes():
    """List all available themes with descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return
    
    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, 'r') as f:
                theme_data = json.load(f)
                display_name = theme_data.get('name', theme_name)
                description = theme_data.get('description', '')
        except:
            display_name = theme_name
            description = ''
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
  python create_map_poster.py --list-themes
        """
    )
    
    parser.add_argument('--city', '-c', type=str, help='City name')
    parser.add_argument('--country', '-C', type=str, help='Country name')
    parser.add_argument('--theme', '-t', type=str, default='feature_based', help='Theme name (default: feature_based)')
    parser.add_argument('--distance', '-d', type=int, default=29000, help='Map radius in meters (default: 29000)')
    parser.add_argument('--ratio', '-r', type=str, default='1:1', help='Aspect ratio as width:height (default: 1:1, e.g., 16:9, 16:12, 12:16)')
    parser.add_argument('--stretch', '-s', action='store_true', help='Stretch mode: download 1:1 data then stretch to target ratio (default: crop mode downloads data matching ratio)')
    parser.add_argument('--landmark', '-l', type=str, help='Landmark name to center the map on (e.g., "Forbidden City", "Tiananmen Square")')
    parser.add_argument('--coords', type=str, help='Custom center coordinates as "lat,lon" (e.g., "39.91,116.40")')
    parser.add_argument('--output-dir', '-o', type=str, default=DEFAULT_OUTPUT_DIR, help='Directory for generated posters (default: generated_posters)')
    parser.add_argument('--list-themes', action='store_true', help='List all available themes')
    
    args = parser.parse_args()
    
    # If no arguments provided, show examples
    if len(os.sys.argv) == 1:
        print_examples()
        os.sys.exit(0)
    
    # List themes if requested
    if args.list_themes:
        list_themes()
        os.sys.exit(0)
    
    # Validate required arguments
    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        os.sys.exit(1)
    
    # Validate theme exists
    available_themes = get_available_themes()
    if args.theme not in available_themes:
        print(f"Error: Theme '{args.theme}' not found.")
        print(f"Available themes: {', '.join(available_themes)}")
        os.sys.exit(1)
    
    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)
    
    # Load theme
    THEME = load_theme(args.theme)
    
    # Get coordinates and generate poster
    try:
        coords = get_coordinates(args.city, args.country, args.landmark, args.coords)
        output_file = generate_output_filename(args.city, args.theme, args.output_dir)
        create_poster(args.city, args.country, coords, args.distance, output_file, args.ratio, args.stretch)
        
        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        os.sys.exit(1)
