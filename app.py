from flask import Flask, render_template_string, request, send_file
from io import BytesIO
import xml.etree.ElementTree as ET
import requests
from datetime import datetime
import time
import threading
import json
import os
import psutil
from threading import Lock

app = Flask(__name__)

# Cache update interval (seconds)
UPDATE_INTERVAL = 120
# Minimum remaining time for PokéStops (seconds)
MIN_REMAINING_TIME = 180
# Maximum remaining time for PokéStops (seconds, to filter invalid data)
MAX_REMAINING_TIME = 7200

# Grunt type configuration (excluding giovanni, arlo, sierra, cliff, showcase, None gender)
POKESTOP_TYPES = {
    'gruntmale': {'ids': [4], 'gender': {4: 'Male'}, 'display': 'Grunt'},
    'gruntfemale': {'ids': [5], 'gender': {5: 'Female'}, 'display': 'Grunt'},
    'bug': {'ids': [6, 7], 'gender': {7: 'Male', 6: 'Female'}, 'display': 'Bug'},
    'dark': {'ids': [10, 11], 'gender': {11: 'Male', 10: 'Female'}, 'display': 'Dark'},
    'dragon': {'ids': [12, 13], 'gender': {13: 'Male', 12: 'Female'}, 'display': 'Dragon'},
    'fairy': {'ids': [14, 15], 'gender': {15: 'Male', 14: 'Female'}, 'display': 'Fairy'},
    'fighting': {'ids': [16, 17], 'gender': {17: 'Male', 16: 'Female'}, 'display': 'Fighting'},
    'fire': {'ids': [18, 19], 'gender': {19: 'Male', 18: 'Female'}, 'display': 'Fire'},
    'flying': {'ids': [20, 21], 'gender': {21: 'Male', 20: 'Female'}, 'display': 'Flying'},
    'grass': {'ids': [22, 23], 'gender': {23: 'Male', 22: 'Female'}, 'display': 'Grass'},
    'ground': {'ids': [24, 25], 'gender': {25: 'Male', 24: 'Female'}, 'display': 'Ground'},
    'ice': {'ids': [26, 27], 'gender': {27: 'Male', 26: 'Female'}, 'display': 'Ice'},
    'metal': {'ids': [28, 29], 'gender': {29: 'Male', 28: 'Female'}, 'display': 'Metal'},
    'normal': {'ids': [30, 31], 'gender': {31: 'Male', 30: 'Female'}, 'display': 'Normal'},
    'poison': {'ids': [32, 33], 'gender': {33: 'Male', 32: 'Female'}, 'display': 'Poison'},
    'psychic': {'ids': [34, 35], 'gender': {35: 'Male', 34: 'Female'}, 'display': 'Psychic'},
    'rock': {'ids': [36, 37], 'gender': {37: 'Male', 36: 'Female'}, 'display': 'Rock'},
    'water': {'ids': [38, 39], 'gender': {39: 'Male', 38: 'Female'}, 'display': 'Water'},
    'electric': {'ids': [48, 49], 'gender': {49: 'Male', 48: 'Female'}, 'display': 'Electric'},
    'ghost': {'ids': [47, 48], 'gender': {47: 'Male', 48: 'Female'}, 'display': 'Ghost'}  # Fixed IDs
}

# API endpoints (Sydney included)
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php',
    'Sydney': 'https://sydneypogomap.com/pokestop.php'
}

# Thread-safe set for active types
active_types = set(['fairy'])
active_types_lock = Lock()

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
                    'stops': {location: [] for location in API_ENDPOINTS.keys()},
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }, f)
            print(f"✅ Initialized cache file at {cache_file}")
    except Exception as e:
        print(f"⚠️ Failed to initialize cache file for {pokestop_type}: {e}")

def update_cache(pokestop_type, type_info):
    """Update cache for a single type and character IDs."""
    cache_file = get_cache_file(pokestop_type)
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    # NordVPN SOCKS5 proxy configuration
    proxy_host = os.environ.get('NORDVPN_PROXY_HOST')
    proxy_user = os.environ.get('NORDVPN_PROXY_USER')
    proxy_pass = os.environ.get('NORDVPN_PROXY_PASS')
    proxy_url = f'socks5://{proxy_user}:{proxy_pass}@{proxy_host}:1080' if proxy_host and proxy_user and proxy_pass else None
    proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
    while True:
        try:
            stops_by_location = {location: [] for location in API_ENDPOINTS.keys()}
            current_time = time.time()

            for location, url in API_ENDPOINTS.items():
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
                    params = {'time': int(current_time * 1000)}
                    response = requests.get(url, params=params, headers=headers, timeout=10, proxies=proxies)
                    response.raise_for_status()
                    data = response.json()
                    meta = data.get('meta', {})
                    time_offset = current_time - int(meta.get('time', current_time))

                    stops = []
                    for stop in data.get('invasions', []):
                        character_id = stop.get('character')
                        grunt_dialogue = stop.get('grunt_dialogue', '').lower()
                        # Debug logging for Ghost-type PokéStops
                        if character_id in [47, 48]:
                            print(f"👻 Ghost Debug: Location={location}, Character ID={character_id}, Dialogue={grunt_dialogue[:50]}, Remaining={stop['invasion_end'] - (current_time - time_offset)}s")
                        # Prioritize dialogue for electric type disambiguation
                        is_electric = (
                            character_id in character_ids and (
                                pokestop_type == 'electric' and any(kw in grunt_dialogue for kw in ['shock', 'electric', 'volt', 'charge'])
                                or pokestop_type != 'electric'
                            )
                        )
                        is_grunt = (
                            pokestop_type.startswith('grunt') and 'grunt' in grunt_dialogue
                        )
                        is_typed = (
                            not pokestop_type.startswith('grunt') and
                            (
                                pokestop_type.lower() in grunt_dialogue or
                                (pokestop_type == 'ghost' and 'ke...ke...' in grunt_dialogue)
                            )
                        )
                        remaining_time = stop['invasion_end'] - (current_time - time_offset)
                        if (
                            (character_id in character_ids or is_grunt or is_typed or is_electric) and
                            MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME
                        ):
                            stops.append({
                                'lat': stop['lat'],
                                'lng': stop['lng'],
                                'name': stop.get('name', f'Unnamed PokéStop ({location})'),
                                'remaining_time': remaining_time,
                                'character': character_id,
                                'type': display_type,
                                'gender': gender_map.get(character_id, 'Unknown'),
                                'grunt_dialogue': grunt_dialogue,
                                'encounter_pokemon_id': stop.get('encounter_pokemon_id', None)
                            })
                        print(f"📡 Debug: {location} ({pokestop_type}) - Character ID: {character_id}, Dialogue: {grunt_dialogue[:50]}...")
                    stops_by_location[location] = stops
                    print(f"✅ Fetched {len(stops_by_location[location])} {display_type} ({pokestop_type}) PokéStops for {location}")
                except Exception as e:
                    print(f"❌ Error fetching data for {location} ({pokestop_type}): {e}")
                time.sleep(2)

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

# Initialize default type cache
initialize_cache('fairy')
threading.Thread(target=update_cache, args=('fairy', POKESTOP_TYPES['fairy']), daemon=True).start()

# HTML template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ pokestop_type.capitalize() }}-Type PokéStops</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="120">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { color: #333; }
        h2 { color: #555; margin-top: 20px; }
        ul { list-style-type: none; padding: 0; }
        li { margin: 10px 0; }
        a { color: #0066cc; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .no-stops { color: #888; }
        .debug { font-size: 0.9em; color: #666; }
    </style>
</head>
<body>
    <h1>{{ pokestop_type.capitalize() }}-Type PokéStops</h1>
    <p>Last updated: {{ last_updated }}</p>
    <p>Updates every 2 minutes. Only PokéStops with more than 3 minutes remaining are shown.</p>
    <p>Switch type:
        {% for type in types %}
            <a href="?type={{ type }}{% if debug %}&debug=true{% endif %}">{{ type.capitalize() }}</a>{% if not loop.last %}, {% endif %}
        {% endfor %}
    </p>
    <p>
        <a href="/download_gpx?type={{ pokestop_type }}" target="_blank">Download GPX (over 10 min remaining)</a>
    </p>
    {% for location, stops in stops.items() %}
        <h2>{{ location }}</h2>
        {% if stops %}
            <ul>
                {% for stop in stops %}
                    <li>{{ stop.type }} ({{ stop.gender }}) {{ stop.name }} (<a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}">{{ stop.lat }}, {{ stop.lng }}</a>) - {{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining
                        {% if debug %}
                            <span class="debug">(Character: {{ stop.character }}, Dialogue: {{ stop.grunt_dialogue|default('N/A') }}, Encounter ID: {{ stop.encounter_pokemon_id|default('N/A') }})</span>
                        {% endif %}
                    </li>
                {% endfor %}
            </ul>
        {% else %}
            <p class="no-stops">No {{ pokestop_type.capitalize() }}-type PokéStops found in {{ location }}.</p>
        {% endif %}
    {% endfor %}
</body>
</html>
"""

# Route for downloading GPX
@app.route('/download_gpx')
def download_gpx():
    pokestop_type = request.args.get('type', 'fairy').lower()
    debug = request.args.get('debug', 'false').lower() == 'true'
    if pokestop_type not in POKESTOP_TYPES:
        pokestop_type = 'fairy'
    cache_file = get_cache_file(pokestop_type)
    
    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ Error reading cache for {pokestop_type}: {e}")
        data = {'stops': {location: [] for location in API_ENDPOINTS.keys()}, 'last_updated': 'Unknown'}
    
    # Filter stops with remaining_time > 600 seconds (10 minutes)
    filtered_stops = []
    for location, stops in data['stops'].items():
        for stop in stops:
            if stop['remaining_time'] > 600:
                filtered_stops.append(stop)
    
    # Generate GPX
    gpx = ET.Element('gpx', version="1.1", creator="Wakestops App")
    for stop in filtered_stops:
        wpt = ET.SubElement(gpx, 'wpt', lat=str(stop['lat']), lon=str(stop['lng']))
        name = ET.SubElement(wpt, 'name')
        name.text = stop['name']
    
    gpx_str = ET.tostring(gpx, encoding='unicode')
    
    # Return as downloadable file
    return send_file(
        BytesIO(gpx_str.encode()),
        mimetype='application/gpx+xml',
        as_attachment=True,
        download_name='pokestops.gpx'
    )

# Temporary debug endpoint to inspect raw API data
@app.route('/debug_api')
def debug_api():
    location = request.args.get('location', 'London')
    url = API_ENDPOINTS.get(location, API_ENDPOINTS['London'])
    try:
        response = requests.get(url, params={'time': int(time.time() * 1000)}, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {'error': str(e)}

@app.route('/')
def get_pokestops():
    pokestop_type = request.args.get('type', 'fairy').lower()
    debug = request.args.get('debug', 'false').lower() == 'true'
    if pokestop_type not in POKESTOP_TYPES:
        pokestop_type = 'fairy'
    cache_file = get_cache_file(pokestop_type)
    type_info = POKESTOP_TYPES[pokestop_type]

    # Start cache thread for new type if not active
    with active_types_lock:
        if pokestop_type not in active_types:
            initialize_cache(pokestop_type)
            threading.Thread(target=update_cache, args=(pokestop_type, type_info), daemon=True).start()
            active_types.add(pokestop_type)
            print(f"🛠️ Started cache thread for {pokestop_type}")

    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        print(f"📖 Debug: Loaded cache for {pokestop_type} from {cache_file}")
    except Exception as e:
        print(f"⚠️ Error reading cache for {pokestop_type}: {e}")
        data = {'stops': {location: [] for location in API_ENDPOINTS.keys()}, 'last_updated': 'Unknown'}

    try:
        return render_template_string(
            HTML_TEMPLATE,
            stops=data.get('stops', {location: [] for location in API_ENDPOINTS.keys()}),
            last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            pokestop_type=pokestop_type,
            types=POKESTOP_TYPES.keys(),
            debug=debug
        )
    except Exception as e:
        print(f"❌ Render failed for {pokestop_type}: {e}")
        return render_template_string(
            HTML_TEMPLATE,
            stops={location: [] for location in API_ENDPOINTS.keys()},
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            pokestop_type=pokestop_type,
            types=POKESTOP_TYPES.keys(),
            debug=debug
        )

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
