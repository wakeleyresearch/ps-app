from flask import Flask, render_template_string, request
import requests
from datetime import datetime
import time
import threading
import json
import os

app = Flask(__name__)

# Cache update interval (seconds)
UPDATE_INTERVAL = 120
# Minimum remaining time for PokéStops (seconds)
MIN_REMAINING_TIME = 180

# Type configuration
POKESTOP_TYPES = {
    'normal': [1, 2],
    'fighting': [3, 4],
    'flying': [5, 6],
    'poison': [7, 8],
    'ground': [9, 10],
    'rock': [11, 12],
    'bug': [13, 14],
    'fairy': [14, 15],
    'ghost': [16, 17],
    'steel': [18, 19],
    'fire': [20, 21],
    'water': [22, 23],
    'grass': [24, 25],
    'electric': [26, 27],
    'psychic': [28, 29],
    'ice': [30, 31],
    'dragon': [32, 33],
    'dark': [34, 35]
}

# API endpoints for each location
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php'
}

def get_cache_file(pokestop_type):
    """Return cache file path for the given type."""
    return f'/app/pokestops_{pokestop_type}.json'

def initialize_cache(pokestop_type):
    """Initialize cache file for the given type."""
    cache_file = get_cache_file(pokestop_type)
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        if not os.path.exists(cache_file):
            with open(cache_file, 'w') as f:
                json.dump({
                    'stops': {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []},
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }, f)
            print(f"✅ Initialized cache file at {cache_file}")
    except Exception as e:
        print(f"⚠️ Failed to initialize cache file for {pokestop_type}: {e}")

def update_cache(pokestop_type, character_ids):
    """Update cache for the given type and character IDs."""
    cache_file = get_cache_file(pokestop_type)
    while True:
        try:
            stops_by_location = {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []}
            current_time = time.time()

            for location, url in API_ENDPOINTS.items():
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
                    params = {'time': int(current_time * 1000)}
                    response = requests.get(url, params=params, headers=headers, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    print(f"📡 Debug: Received data for {location} ({pokestop_type}): {json.dumps(data, indent=2)}")
                    meta = data.get('meta', {})
                    time_offset = current_time - int(meta.get('time', current_time))

                    stops = [
                        {
                            'lat': stop['lat'],
                            'lng': stop['lng'],
                            'name': stop.get('name', f'Unnamed PokéStop ({location})'),
                            'remaining_time': stop['invasion_end'] - (current_time - time_offset),
                            'character': stop.get('character')
                        }
                        for stop in data.get('invasions', [])
                        # Comment out the next line to debug all {pokestop_type}-type PokéStops (ignore time filter)
                        # if stop.get('character') in character_ids
                        if stop.get('character') in character_ids and (stop['invasion_end'] - (current_time - time_offset)) > MIN_REMAINING_TIME
                    ]
                    stops_by_location[location] = [
                        {k: v for k, v in stop.items() if k != 'character'} for stop in stops
                    ]
                    print(f"✅ Fetched {len(stops_by_location[location])} {pokestop_type.capitalize()}-type PokéStops for {location}")
                except Exception as e:
                    print(f"❌ Error fetching data for {location} ({pokestop_type}): {e}")
                time.sleep(1)  # Delay to avoid rate limits

            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                with open(cache_file, 'w') as f:
                    json.dump({
                        'stops': stops_by_location,
                        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }, f)
                print(f"✅ Cache updated for {pokestop_type} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception as e:
                print(f"⚠️ Error writing cache for {pokestop_type}: {e}")
        except Exception as e:
            print(f"❌ Error updating cache for {pokestop_type}: {e}")
        time.sleep(UPDATE_INTERVAL)

# Start cache update threads for all types
for pokestop_type, character_ids in POKESTOP_TYPES.items():
    initialize_cache(pokestop_type)
    threading.Thread(target=update_cache, args=(pokestop_type, character_ids), daemon=True).start()

# HTML template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ pokestop_type.capitalize() }}-Type PokéStops</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="120"> <!-- Refresh every 120 seconds -->
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { color: #333; }
        h2 { color: #555; margin-top: 20px; }
        ul { list-style-type: none; padding: 0; }
        li { margin: 10px 0; }
        a { color: #0066cc; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .no-stops { color: #888; }
    </style>
</head>
<body>
    <h1>{{ pokestop_type.capitalize() }}-Type PokéStops</h1>
    <p>Last updated: {{ last_updated }}</p>
    <p>Updates every 2 minutes. Only PokéStops with more than 3 minutes remaining are shown.</p>
    <p>Switch type: 
    {% for type in types %}
        <a href="?type={{ type }}">{{ type.capitalize() }}</a>{% if not loop.last %}, {% endif %}
    {% endfor %}
    </p>
    {% for location, stops in stops.items() %}
        <h2>{{ location }}</h2>
        {% if stops %}
            <ul>
            {% for stop in stops %}
                <li>{{ stop.name }} (<a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}">{{ stop.lat }}, {{ stop.lng }}</a>) - {{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining</li>
            {% endfor %}
            </ul>
        {% else %}
            <p class="no-stops">No {{ pokestop_type.capitalize() }}-type PokéStops found in {{ location }}.</p>
        {% endif %}
    {% endfor %}
</body>
</html>
"""

@app.route('/')
def get_pokestops():
    # Get type from query parameter, default to 'fire'
    pokestop_type = request.args.get('type', 'fire').lower()
    if pokestop_type not in POKESTOP_TYPES:
        pokestop_type = 'fire'  # Fallback to fire if invalid
    cache_file = get_cache_file(pokestop_type)

    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        print(f"📖 Debug: Loaded cache for {pokestop_type} from {cache_file}")
        return render_template_string(
            HTML_TEMPLATE,
            stops=data.get('stops', {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []}),
            last_updated=data.get('last_updated', 'Unknown'),
            pokestop_type=pokestop_type,
            types=POKESTOP_TYPES.keys()
        )
    except Exception as e:
        print(f"⚠️ Error reading cache for {pokestop_type}: {e}")
        # Fallback to fetching data
        try:
            stops_by_location = {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []}
            current_time = time.time()
            character_ids = POKESTOP_TYPES[pokestop_type]
            for location, url in API_ENDPOINTS.items():
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
                    params = {'time': int(current_time * 1000)}
                    response = requests.get(url, params=params, headers=headers, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    print(f"📡 Debug: Fallback fetch for {location} ({pokestop_type}): {json.dumps(data, indent=2)}")
                    meta = data.get('meta', {})
                    time_offset = current_time - int(meta.get('time', current_time))
                    stops = [
                        {
                            'lat': stop['lat'],
                            'lng': stop['lng'],
                            'name': stop.get('name', f'Unnamed PokéStop ({location})'),
                            'remaining_time': stop['invasion_end'] - (current_time - time_offset),
                            'character': stop.get('character')
                        }
                        for stop in data.get('invasions', [])
                        # Comment out the next line to debug all {pokestop_type}-type PokéStops
                        # if stop.get('character') in character_ids
                        if stop.get('character') in character_ids and (stop['invasion_end'] - (current_time - time_offset)) > MIN_REMAINING_TIME
                    ]
                    stops_by_location[location] = [
                        {k: v for k, v in stop.items() if k != 'character'} for stop in stops
                    ]
                except Exception as e:
                    print(f"❌ Error in fallback fetch for {location} ({pokestop_type}): {e}")
                time.sleep(1)  # Delay to avoid rate limits
            return render_template_string(
                HTML_TEMPLATE,
                stops=stops_by_location,
                last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                pokestop_type=pokestop_type,
                types=POKESTOP_TYPES.keys()
            )
        except Exception as e:
            print(f"❌ Fallback failed for {pokestop_type}: {e}")
            return render_template_string(
                HTML_TEMPLATE,
                stops={'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []},
                last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                pokestop_type=pokestop_type,
                types=POKESTOP_TYPES.keys()
            )

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))  # Dynamic port for Render
    app.run(host='0.0.0.0', port=port, debug=False)  # Disable debug for production