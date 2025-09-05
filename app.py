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

# Grunt type configuration - Only grunt and water are separated by gender
# All other types combine both genders
POKESTOP_TYPES = {
    # Grunt types separated by gender
    'gruntmale': {'ids': [4], 'gender': {4: 'Male'}, 'display': 'Grunt (Male)'},
    'gruntfemale': {'ids': [5], 'gender': {5: 'Female'}, 'display': 'Grunt (Female)'},
    
    # Water types separated by gender  
    'watermale': {'ids': [39], 'gender': {39: 'Male'}, 'display': 'Water (Male)'},
    'waterfemale': {'ids': [38], 'gender': {38: 'Female'}, 'display': 'Water (Female)'},
    
    # All other types combined (both genders)
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
    'electric': {'ids': [48, 49], 'gender': {49: 'Male', 48: 'Female'}, 'display': 'Electric'},
    'ghost': {'ids': [46, 47], 'gender': {47: 'Male', 46: 'Female'}, 'display': 'Ghost'}
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
                        
                        # Skip if not in our character IDs
                        if character_id not in character_ids:
                            continue
                            
                        remaining_time = stop['invasion_end'] - (current_time - time_offset)
                        
                        # Check time validity
                        if not (MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME):
                            continue
                        
                        # For typed grunts (not generic grunts), check dialogue
                        if pokestop_type not in ['gruntmale', 'gruntfemale']:
                            # Special cases for dialogue matching
                            dialogue_matches = False
                            
                            if pokestop_type == 'ghost' and 'ke...ke...' in grunt_dialogue:
                                dialogue_matches = True
                            elif pokestop_type == 'electric' and any(kw in grunt_dialogue for kw in ['shock', 'electric', 'volt', 'charge']):
                                dialogue_matches = True
                            elif pokestop_type.replace('male', '').replace('female', '') in grunt_dialogue:
                                dialogue_matches = True
                            
                            if not dialogue_matches:
                                continue
                        
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
                    
                    stops_by_location[location] = stops
                    print(f"✅ Fetched {len(stops)} {display_type} PokéStops for {location}")
                    
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
    <title>{{ pokestop_type_display }} PokéStops</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="120">
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 { 
            color: #333;
            margin-bottom: 10px;
        }
        h2 { 
            color: #555;
            margin-top: 30px;
            border-bottom: 2px solid #eee;
            padding-bottom: 10px;
        }
        .info-text {
            color: #666;
            margin: 10px 0;
        }
        .type-selector {
            margin: 20px 0;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 5px;
        }
        .type-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }
        .type-btn {
            padding: 8px 16px;
            background: white;
            border: 1px solid #ddd;
            border-radius: 4px;
            text-decoration: none;
            color: #333;
            transition: all 0.2s;
        }
        .type-btn:hover {
            background: #007bff;
            color: white;
            border-color: #007bff;
        }
        .type-btn.active {
            background: #007bff;
            color: white;
            border-color: #007bff;
        }
        ul { 
            list-style-type: none;
            padding: 0;
        }
        li { 
            margin: 12px 0;
            padding: 10px;
            background: #f8f9fa;
            border-radius: 4px;
            border-left: 3px solid #007bff;
        }
        a { 
            color: #007bff;
            text-decoration: none;
        }
        a:hover { 
            text-decoration: underline;
        }
        .no-stops { 
            color: #888;
            font-style: italic;
            padding: 20px;
            text-align: center;
            background: #f8f9fa;
            border-radius: 4px;
        }
        .debug { 
            font-size: 0.85em;
            color: #999;
            margin-top: 5px;
            font-family: monospace;
        }
        .download-btn {
            display: inline-block;
            margin: 10px 0;
            padding: 10px 20px;
            background: #28a745;
            color: white;
            border-radius: 4px;
            text-decoration: none;
            font-weight: 500;
        }
        .download-btn:hover {
            background: #218838;
            text-decoration: none;
        }
        .sort-btn {
            padding: 6px 12px;
            background: #6c757d;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            margin-left: 10px;
        }
        .sort-btn:hover {
            background: #5a6268;
        }
    </style>
    <script>
        var stopsData = {
            {% for location in stops.keys() %}
            '{{ location }}': {{ stops[location] | tojson }},
            {% endfor %}
        };
        var isDebug = {{ debug | tojson }};
        var sortMode = {};
        
        function distance(a, b) {
            const R = 6371; // Earth radius in km
            const dLat = (b.lat - a.lat) * Math.PI / 180;
            const dLon = (b.lng - a.lng) * Math.PI / 180;
            const lat1 = a.lat * Math.PI / 180;
            const lat2 = b.lat * Math.PI / 180;
            const x = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.sin(dLon / 2) * Math.sin(dLon / 2) * Math.cos(lat1) * Math.cos(lat2);
            const c = 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
            return R * c;
        }
        
        function nearestNeighbor(points) {
            if (points.length <= 1) return points;
            // Sort by remaining_time descending to choose starting point
            points.sort((a, b) => b.remaining_time - a.remaining_time);
            let ordered = [points.shift()]; // Start with the one having the most time remaining
            while (points.length > 0) {
                let last = ordered[ordered.length - 1];
                let minDist = Infinity;
                let closestIdx = -1;
                for (let i = 0; i < points.length; i++) {
                    let dist = distance(last, points[i]);
                    if (dist < minDist) {
                        minDist = dist;
                        closestIdx = i;
                    }
                }
                ordered.push(points.splice(closestIdx, 1)[0]);
            }
            return ordered;
        }
        
        function renderStops(location, stops) {
            let ul = document.getElementById('stops-list-' + location);
            ul.innerHTML = '';
            stops.forEach(stop => {
                let li = document.createElement('li');
                let html = `${stop.type} (${stop.gender}) ${stop.name} (<a href="https://maps.google.com/?q=${stop.lat},${stop.lng}">${stop.lat.toFixed(6)}, ${stop.lng.toFixed(6)}</a>) - ${Math.floor(stop.remaining_time / 60)} min ${stop.remaining_time % 60} sec remaining`;
                if (isDebug) {
                    html += `<div class="debug">Character: ${stop.character}, Dialogue: ${stop.grunt_dialogue || 'N/A'}, Encounter ID: ${stop.encounter_pokemon_id || 'N/A'}</div>`;
                }
                li.innerHTML = html;
                ul.appendChild(li);
            });
        }
        
        function toggleSort(location) {
            sortMode[location] = sortMode[location] === 'nearest' ? 'time' : 'nearest';
            let button = document.getElementById('sort-btn-' + location);
            button.textContent = sortMode[location] === 'nearest' ? 'Sort by Time' : 'Sort by Route';
            let stops = [...stopsData[location]];
            if (sortMode[location] === 'nearest') {
                stops = nearestNeighbor(stops);
            } else {
                stops.sort((a, b) => b.remaining_time - a.remaining_time);
            }
            renderStops(location, stops);
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>{{ pokestop_type_display }} PokéStops</h1>
        <p class="info-text">Last updated: {{ last_updated }}</p>
        <p class="info-text">Updates every 2 minutes. Only PokéStops with 3-120 minutes remaining are shown.</p>
        
        <div class="type-selector">
            <strong>Select Type:</strong>
            <div class="type-buttons">
                <!-- Gender-separated types -->
                <a href="?type=gruntmale{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'gruntmale' %}active{% endif %}">Grunt (Male)</a>
                <a href="?type=gruntfemale{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'gruntfemale' %}active{% endif %}">Grunt (Female)</a>
                <a href="?type=watermale{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'watermale' %}active{% endif %}">Water (Male)</a>
                <a href="?type=waterfemale{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'waterfemale' %}active{% endif %}">Water (Female)</a>
                
                <!-- Combined types (alphabetical) -->
                <a href="?type=bug{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'bug' %}active{% endif %}">Bug</a>
                <a href="?type=dark{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'dark' %}active{% endif %}">Dark</a>
                <a href="?type=dragon{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'dragon' %}active{% endif %}">Dragon</a>
                <a href="?type=electric{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'electric' %}active{% endif %}">Electric</a>
                <a href="?type=fairy{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'fairy' %}active{% endif %}">Fairy</a>
                <a href="?type=fighting{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'fighting' %}active{% endif %}">Fighting</a>
                <a href="?type=fire{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'fire' %}active{% endif %}">Fire</a>
                <a href="?type=flying{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'flying' %}active{% endif %}">Flying</a>
                <a href="?type=ghost{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'ghost' %}active{% endif %}">Ghost</a>
                <a href="?type=grass{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'grass' %}active{% endif %}">Grass</a>
                <a href="?type=ground{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'ground' %}active{% endif %}">Ground</a>
                <a href="?type=ice{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'ice' %}active{% endif %}">Ice</a>
                <a href="?type=metal{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'metal' %}active{% endif %}">Metal</a>
                <a href="?type=normal{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'normal' %}active{% endif %}">Normal</a>
                <a href="?type=poison{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'poison' %}active{% endif %}">Poison</a>
                <a href="?type=psychic{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'psychic' %}active{% endif %}">Psychic</a>
                <a href="?type=rock{% if debug %}&debug=true{% endif %}" class="type-btn {% if pokestop_type == 'rock' %}active{% endif %}">Rock</a>
            </div>
        </div>
        
        <a href="/download_gpx?type={{ pokestop_type }}" target="_blank" class="download-btn">Download GPX (10+ min remaining)</a>
        
        {% for location, location_stops in stops.items() %}
            <h2>{{ location }}
                {% if location_stops %}
                    <button id="sort-btn-{{ location }}" onclick="toggleSort('{{ location }}')" class="sort-btn">Sort by Route</button>
                {% endif %}
            </h2>
            {% if location_stops %}
                <ul id="stops-list-{{ location }}">
                    {% for stop in location_stops %}
                        <li>{{ stop.type }} ({{ stop.gender }}) {{ stop.name }} (<a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}">{{ "%.6f"|format(stop.lat) }}, {{ "%.6f"|format(stop.lng) }}</a>) - {{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining
                            {% if debug %}
                                <div class="debug">Character: {{ stop.character }}, Dialogue: {{ stop.grunt_dialogue|default('N/A') }}, Encounter ID: {{ stop.encounter_pokemon_id|default('N/A') }}</div>
                            {% endif %}
                        </li>
                    {% endfor %}
                </ul>
            {% else %}
                <p class="no-stops">No {{ pokestop_type_display }} PokéStops found in {{ location }}.</p>
            {% endif %}
        {% endfor %}
    </div>
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
        download_name=f'pokestops_{pokestop_type}.gpx'
    )

@app.route('/')
def get_pokestops():
    pokestop_type = request.args.get('type', 'fairy').lower()
    debug = request.args.get('debug', 'false').lower() == 'true'
    if pokestop_type not in POKESTOP_TYPES:
        pokestop_type = 'fairy'
    cache_file = get_cache_file(pokestop_type)
    type_info = POKESTOP_TYPES[pokestop_type]

    # Check if cache exists and is recent (less than 5 minutes old)
    cache_exists = False
    cache_recent = False
    try:
        if os.path.exists(cache_file):
            cache_exists = True
            # Check if cache is recent
            cache_age = time.time() - os.path.getmtime(cache_file)
            if cache_age < 300:  # 5 minutes
                cache_recent = True
    except:
        pass

    # Start cache thread for new type if not active
    with active_types_lock:
        if pokestop_type not in active_types:
            initialize_cache(pokestop_type)
            
            # If cache doesn't exist or is old, fetch initial data synchronously
            if not cache_exists or not cache_recent:
                print(f"📥 Fetching initial data for {pokestop_type}...")
                fetch_initial_data(pokestop_type, type_info)
            
            # Start background update thread
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

    # Sort stops by remaining_time descending (default sort)
    stops = data.get('stops', {location: [] for location in API_ENDPOINTS.keys()})
    for location in stops:
        stops[location] = sorted(stops[location], key=lambda s: s['remaining_time'], reverse=True)

    return render_template_string(
        HTML_TEMPLATE,
        stops=stops,
        last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        pokestop_type=pokestop_type,
        pokestop_type_display=type_info['display'],
        types=POKESTOP_TYPES.keys(),
        debug=debug
    )

def fetch_initial_data(pokestop_type, type_info):
    """Fetch initial data for a type synchronously (single location for speed)."""
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    cache_file = get_cache_file(pokestop_type)
    
    # Just fetch from one location for initial load (NYC is usually reliable)
    try:
        url = API_ENDPOINTS['NYC']
        current_time = time.time()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
        params = {'time': int(current_time * 1000)}
        
        response = requests.get(url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        meta = data.get('meta', {})
        time_offset = current_time - int(meta.get('time', current_time))
        
        stops = []
        for stop in data.get('invasions', []):
            character_id = stop.get('character')
            grunt_dialogue = stop.get('grunt_dialogue', '').lower()
            
            if character_id not in character_ids:
                continue
                
            remaining_time = stop['invasion_end'] - (current_time - time_offset)
            
            if not (MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME):
                continue
            
            # For typed grunts (not generic grunts), check dialogue
            if pokestop_type not in ['gruntmale', 'gruntfemale']:
                dialogue_matches = False
                
                if pokestop_type == 'ghost' and 'ke...ke...' in grunt_dialogue:
                    dialogue_matches = True
                elif pokestop_type == 'electric' and any(kw in grunt_dialogue for kw in ['shock', 'electric', 'volt', 'charge']):
                    dialogue_matches = True
                elif pokestop_type.replace('male', '').replace('female', '') in grunt_dialogue:
                    dialogue_matches = True
                
                if not dialogue_matches:
                    continue
            
            stops.append({
                'lat': stop['lat'],
                'lng': stop['lng'],
                'name': stop.get('name', 'Unnamed PokéStop'),
                'remaining_time': remaining_time,
                'character': character_id,
                'type': display_type,
                'gender': gender_map.get(character_id, 'Unknown'),
                'grunt_dialogue': grunt_dialogue,
                'encounter_pokemon_id': stop.get('encounter_pokemon_id', None)
            })
        
        # Write initial data to cache
        initial_data = {
            'stops': {
                'NYC': stops,
                'Vancouver': [],
                'Singapore': [],
                'London': [],
                'Sydney': []
            },
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, 'w') as f:
            json.dump(initial_data, f)
        
        print(f"✅ Initial data fetched for {pokestop_type}: {len(stops)} stops from NYC")
        
    except Exception as e:
        print(f"⚠️ Failed to fetch initial data for {pokestop_type}: {e}")
        # Create empty cache to prevent loading issues
        initial_data = {
            'stops': {location: [] for location in API_ENDPOINTS.keys()},
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        try:
            with open(cache_file, 'w') as f:
                json.dump(initial_data, f)
        except:
            pass

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
