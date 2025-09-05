from flask import Flask, render_template_string, request
import requests
from datetime import datetime
import time
import threading
import json
import os
import psutil
from threading import Lock
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cache update interval (seconds)
UPDATE_INTERVAL = 120
# Minimum remaining time for PokéStops (seconds)
MIN_REMAINING_TIME = 180
# Maximum remaining time for PokéStops (seconds, to filter invalid data)
MAX_REMAINING_TIME = 7200

# Maximum concurrent scrapers
MAX_CONCURRENT_TYPES = 3  # Scrape up to 3 types simultaneously
MAX_CONCURRENT_LOCATIONS = 5  # Scrape all 5 locations in parallel for each type

# Grunt type configuration - Fixed ghost IDs from [47, 46] to [47, 48]
POKESTOP_TYPES = {
    # Grunt types separated by gender
    'gruntmale': {'ids': [4], 'gender': {4: 'Male'}, 'display': 'Grunt (Male)', 'dialogue_keywords': ['grunt']},
    'gruntfemale': {'ids': [5], 'gender': {5: 'Female'}, 'display': 'Grunt (Female)', 'dialogue_keywords': ['grunt']},
    
    # Water types separated by gender  
    'watermale': {'ids': [39], 'gender': {39: 'Male'}, 'display': 'Water (Male)', 'dialogue_keywords': ['water', 'splash', 'ocean', 'sea']},
    'waterfemale': {'ids': [38], 'gender': {38: 'Female'}, 'display': 'Water (Female)', 'dialogue_keywords': ['water', 'splash', 'ocean', 'sea']},
    
    # All other types combined (both genders) with dialogue keywords for fallback
    'bug': {'ids': [6, 7], 'gender': {7: 'Male', 6: 'Female'}, 'display': 'Bug', 'dialogue_keywords': ['bug', 'insect', 'creepy', 'crawl']},
    'dark': {'ids': [10, 11], 'gender': {11: 'Male', 10: 'Female'}, 'display': 'Dark', 'dialogue_keywords': ['dark', 'shadow', 'night']},
    'dragon': {'ids': [12, 13], 'gender': {13: 'Male', 12: 'Female'}, 'display': 'Dragon', 'dialogue_keywords': ['dragon', 'roar', 'legendary']},
    'fairy': {'ids': [14, 15], 'gender': {15: 'Male', 14: 'Female'}, 'display': 'Fairy', 'dialogue_keywords': ['fairy', 'cute', 'adorable', 'charm']},
    'fighting': {'ids': [16, 17], 'gender': {17: 'Male', 16: 'Female'}, 'display': 'Fighting', 'dialogue_keywords': ['fighting', 'muscle', 'combat', 'battle', 'strength']},
    'fire': {'ids': [18, 19], 'gender': {19: 'Male', 18: 'Female'}, 'display': 'Fire', 'dialogue_keywords': ['fire', 'burn', 'flame', 'heat', 'hot']},
    'flying': {'ids': [20, 21], 'gender': {21: 'Male', 20: 'Female'}, 'display': 'Flying', 'dialogue_keywords': ['flying', 'fly', 'wind', 'sky', 'bird']},
    'grass': {'ids': [22, 23], 'gender': {23: 'Male', 22: 'Female'}, 'display': 'Grass', 'dialogue_keywords': ['grass', 'plant', 'nature', 'bloom']},
    'ground': {'ids': [24, 25], 'gender': {25: 'Male', 24: 'Female'}, 'display': 'Ground', 'dialogue_keywords': ['ground', 'earth', 'mud', 'dirt']},
    'ice': {'ids': [26, 27], 'gender': {27: 'Male', 26: 'Female'}, 'display': 'Ice', 'dialogue_keywords': ['ice', 'freeze', 'cold', 'frozen', 'chill']},
    'metal': {'ids': [28, 29], 'gender': {29: 'Male', 28: 'Female'}, 'display': 'Metal', 'dialogue_keywords': ['metal', 'steel', 'iron', 'hard']},
    'normal': {'ids': [30, 31], 'gender': {31: 'Male', 30: 'Female'}, 'display': 'Normal', 'dialogue_keywords': ['normal', 'ordinary']},
    'poison': {'ids': [32, 33], 'gender': {33: 'Male', 32: 'Female'}, 'display': 'Poison', 'dialogue_keywords': ['poison', 'toxic', 'venom']},
    'psychic': {'ids': [34, 35], 'gender': {35: 'Male', 34: 'Female'}, 'display': 'Psychic', 'dialogue_keywords': ['psychic', 'mind', 'telekinesis', 'unseen']},
    'rock': {'ids': [36, 37], 'gender': {37: 'Male', 36: 'Female'}, 'display': 'Rock', 'dialogue_keywords': ['rock', 'stone', 'boulder']},
    'ghost': {'ids': [47, 48], 'gender': {47: 'Male', 48: 'Female'}, 'display': 'Ghost', 'dialogue_keywords': ['ghost', 'ke...ke...', 'boo', 'spirit']},  # Fixed IDs
    'electric': {'ids': [48, 49], 'gender': {49: 'Male', 48: 'Female'}, 'display': 'Electric', 'dialogue_keywords': ['electric', 'shock', 'volt', 'charge', 'zap', 'thunder', 'spark']}
}

# API endpoints (Sydney included)
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php',
    'Sydney': 'https://sydneypogomap.com/pokestop.php'
}

# Cooldown time mapping based on distance (in km)
COOLDOWN_MAP = [
    (1, 1),      # 1km: <1 min (using 1)
    (2, 1),      # 2km: 1 min
    (3, 2),      # 3km: <2 min (using 2)
    (5, 2),      # 5km: 2 min
    (7, 5),      # 7km: 5 min
    (9, 7),      # 9km: <7 min (using 7)
    (10, 7),     # 10km: 7 min
    (12, 8),     # 12km: 8 min
    (18, 10),    # 18km: 10 min
    (26, 15),    # 26km: 15 min
    (42, 19),    # 42km: 19 min
    (65, 22),    # 65km: 22 min
    (76, 25),    # 76km: <25 min (using 25)
    (81, 25),    # 81km: 25 min
    (90, 35),    # 90km: 35 min
    (220, 40),   # 220km: <40 min
    (250, 45),   # 250km: 45 min
    (350, 51),   # 350km: <51 min
    (375, 54),   # 375km: 54 min
    (460, 62),   # 460km: 62 min
    (500, 65),   # 500km: <65 min
    (565, 69),   # 565km: 69 min
    (700, 78),   # 700km: 78 min
    (800, 84),   # 800km: 84 min
    (900, 92),   # 900km: 92 min
    (1000, 99),  # 1000km: 99 min
    (1100, 107), # 1100km: 107 min
    (1200, 114), # 1200km: <114 min
    (1300, 117), # 1300km: 117 min
    (1350, 120), # 1350km: 2 hours
]

def get_cooldown_time(distance_km):
    """Get cooldown time in minutes for a given distance in km"""
    for dist, cooldown in COOLDOWN_MAP:
        if distance_km <= dist:
            return cooldown
    return 120  # Max cooldown for distances > 1350km

# Thread-safe set for active types
active_types = set()
active_types_lock = Lock()
type_update_executors = {}  # Track executors for each type

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
            logger.info(f"✅ Initialized cache file for {pokestop_type} at {cache_file}")
    except Exception as e:
        logger.error(f"⚠️ Failed to initialize cache file for {pokestop_type}: {e}")

def fetch_location(location, url, character_ids, gender_map, display_type, pokestop_type, type_info):
    """Fetch data for a single location."""
    try:
        # NordVPN SOCKS5 proxy configuration
        proxy_host = os.environ.get('NORDVPN_PROXY_HOST')
        proxy_user = os.environ.get('NORDVPN_PROXY_USER')
        proxy_pass = os.environ.get('NORDVPN_PROXY_PASS')
        proxy_url = f'socks5://{proxy_user}:{proxy_pass}@{proxy_host}:1080' if proxy_host and proxy_user and proxy_pass else None
        proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
        current_time = time.time()
        params = {'time': int(current_time * 1000)}
        response = requests.get(url, params=params, headers=headers, timeout=15, proxies=proxies)
        response.raise_for_status()
        data = response.json()
        meta = data.get('meta', {})
        time_offset = current_time - int(meta.get('time', current_time))

        stops = []
        invasion_count = len(data.get('invasions', []))
        dialogue_keywords = type_info.get('dialogue_keywords', [])
        
        logger.info(f"📊 {location}: Found {invasion_count} total invasions for {pokestop_type}")
        
        for stop in data.get('invasions', []):
            character_id = stop.get('character')
            grunt_dialogue = stop.get('grunt_dialogue', '').lower()
            
            # Primary filter: Character ID
            if character_id not in character_ids:
                continue
                
            remaining_time = stop['invasion_end'] - (current_time - time_offset)
            
            # Check time validity
            if not (MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME):
                continue
            
            # Fallback dialogue validation for edge cases
            # This helps catch misidentified types and improves accuracy
            dialogue_match = False
            
            # Special handling for ghost type with "ke...ke..." dialogue
            if pokestop_type == 'ghost' and 'ke' in grunt_dialogue:
                dialogue_match = True
                logger.debug(f"👻 Ghost match - ID: {character_id}, Dialogue: {grunt_dialogue[:50]}")
            # Special handling for electric (shares ID 48 with ghost)
            elif pokestop_type == 'electric' and character_id == 48:
                # For ID 48, use dialogue to distinguish electric from ghost
                if any(kw in grunt_dialogue for kw in dialogue_keywords):
                    dialogue_match = True
                else:
                    # Skip ID 48 if dialogue doesn't match electric keywords
                    logger.debug(f"⚡ Skipping ID 48 for electric - no keyword match in: {grunt_dialogue[:50]}")
                    continue
            # For other types, dialogue is optional validation
            elif dialogue_keywords and any(kw in grunt_dialogue for kw in dialogue_keywords):
                dialogue_match = True
            
            # Log if we have a character match but no dialogue match (for debugging)
            if not dialogue_match and pokestop_type not in ['gruntmale', 'gruntfemale']:
                logger.debug(f"⚠️ {pokestop_type} - Character ID {character_id} matched but no dialogue keywords found: {grunt_dialogue[:50]}")
            
            stops.append({
                'lat': stop['lat'],
                'lng': stop['lng'],
                'name': stop.get('name', f'Unnamed PokéStop ({location})'),
                'remaining_time': remaining_time,
                'character': character_id,
                'type': display_type,
                'gender': gender_map.get(character_id, 'Unknown'),
                'grunt_dialogue': grunt_dialogue,
                'encounter_pokemon_id': stop.get('encounter_pokemon_id', None),
                'dialogue_match': dialogue_match  # Track if dialogue matched for debugging
            })
        
        logger.info(f"✅ Fetched {len(stops)} {display_type} PokéStops for {location}")
        return location, stops
        
    except Exception as e:
        logger.error(f"❌ Error fetching data for {location} ({pokestop_type}): {e}")
        return location, []

def update_cache(pokestop_type, type_info):
    """Update cache for a single type using parallel location fetching."""
    cache_file = get_cache_file(pokestop_type)
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    
    while True:
        try:
            stops_by_location = {}
            
            # Fetch all locations in parallel
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LOCATIONS) as executor:
                futures = []
                for location, url in API_ENDPOINTS.items():
                    future = executor.submit(
                        fetch_location, 
                        location, url, character_ids, gender_map, 
                        display_type, pokestop_type, type_info
                    )
                    futures.append(future)
                
                # Collect results as they complete
                for future in as_completed(futures):
                    location, stops = future.result()
                    stops_by_location[location] = stops

            # Write cache
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                with open(cache_file, 'w') as f:
                    json.dump({
                        'stops': stops_by_location,
                        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }, f)
                logger.info(f"✅ Cache updated for {pokestop_type} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception as e:
                logger.error(f"⚠️ Error writing cache for {pokestop_type}: {e}")
        except Exception as e:
            logger.error(f"❌ Error updating cache for {pokestop_type}: {e}")
        
        time.sleep(UPDATE_INTERVAL)

# Initialize and start updating default types
DEFAULT_TYPES = ['fairy', 'gruntmale', 'gruntfemale']  # Start with these types active

def initialize_default_types():
    """Initialize default types on startup."""
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TYPES) as executor:
        for pokestop_type in DEFAULT_TYPES:
            if pokestop_type in POKESTOP_TYPES:
                initialize_cache(pokestop_type)
                executor.submit(update_cache, pokestop_type, POKESTOP_TYPES[pokestop_type])
                active_types.add(pokestop_type)
                logger.info(f"🚀 Started background updater for {pokestop_type}")

# Start default types on module load
threading.Thread(target=initialize_default_types, daemon=True).start()

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
        .active-types {
            background: #e8f4f8;
            padding: 10px;
            border-radius: 4px;
            margin: 10px 0;
            font-size: 0.9em;
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
        .distance-info {
            color: #28a745;
            font-weight: 600;
            margin-left: 10px;
        }
        .cooldown-info {
            color: #dc3545;
            font-weight: 600;
            margin-left: 5px;
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
        
        // Cooldown time mapping
        var cooldownMap = [
            [1, 1], [2, 1], [3, 2], [5, 2], [7, 5], [9, 7], [10, 7], [12, 8],
            [18, 10], [26, 15], [42, 19], [65, 22], [76, 25], [81, 25], [90, 35],
            [220, 40], [250, 45], [350, 51], [375, 54], [460, 62], [500, 65],
            [565, 69], [700, 78], [800, 84], [900, 92], [1000, 99], [1100, 107],
            [1200, 114], [1300, 117], [1350, 120]
        ];
        
        function getCooldownTime(distanceKm) {
            for (var i = 0; i < cooldownMap.length; i++) {
                if (distanceKm <= cooldownMap[i][0]) {
                    return cooldownMap[i][1];
                }
            }
            return 120; // Max cooldown
        }
        
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
                // Store the distance to next in the current stop
                last.distanceToNext = minDist;
                ordered.push(points.splice(closestIdx, 1)[0]);
            }
            return ordered;
        }
        
        function renderStops(location, stops, isNearest) {
            let ul = document.getElementById('stops-list-' + location);
            ul.innerHTML = '';
            stops.forEach((stop, index) => {
                let li = document.createElement('li');
                let html = `${stop.type} (${stop.gender}) ${stop.name} (<a href="https://maps.google.com/?q=${stop.lat},${stop.lng}">${stop.lat.toFixed(6)}, ${stop.lng.toFixed(6)}</a>) - ${Math.floor(stop.remaining_time / 60)} min ${stop.remaining_time % 60} sec remaining`;
                
                // Add distance and cooldown if sorted by nearest neighbor
                if (isNearest && stop.distanceToNext !== undefined && index < stops.length - 1) {
                    let distKm = stop.distanceToNext.toFixed(1);
                    let cooldown = getCooldownTime(stop.distanceToNext);
                    html += `<span class="distance-info">- ${distKm}km to next</span>`;
                    html += `<span class="cooldown-info">(${cooldown}min)</span>`;
                }
                
                if (isDebug) {
                    html += `<div class="debug">Character: ${stop.character}, Dialogue: ${stop.grunt_dialogue || 'N/A'}, Dialogue Match: ${stop.dialogue_match || 'N/A'}, Encounter ID: ${stop.encounter_pokemon_id || 'N/A'}</div>`;
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
            let isNearest = sortMode[location] === 'nearest';
            if (isNearest) {
                stops = nearestNeighbor(stops);
            } else {
                stops.sort((a, b) => b.remaining_time - a.remaining_time);
            }
            renderStops(location, stops, isNearest);
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>{{ pokestop_type_display }} PokéStops</h1>
        <p class="info-text">Last updated: {{ last_updated }}</p>
        <p class="info-text">Updates every 2 minutes. Only PokéStops with 3-120 minutes remaining are shown.</p>
        <div class="active-types">
            <strong>Currently updating types:</strong> {{ active_types_list }}
        </div>
        
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
                                <div class="debug">Character: {{ stop.character }}, Dialogue: {{ stop.grunt_dialogue|default('N/A') }}, Dialogue Match: {{ stop.dialogue_match|default('N/A') }}, Encounter ID: {{ stop.encounter_pokemon_id|default('N/A') }}</div>
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

    # Start cache thread for new type if not active (limit concurrent types)
    with active_types_lock:
        if pokestop_type not in active_types:
            # Check if we're at max concurrent types
            if len(active_types) >= MAX_CONCURRENT_TYPES:
                logger.warning(f"⚠️ Max concurrent types ({MAX_CONCURRENT_TYPES}) reached. Type {pokestop_type} will be queued.")
            else:
                initialize_cache(pokestop_type)
                
                # If cache doesn't exist or is old, fetch initial data synchronously
                if not cache_exists or not cache_recent:
                    logger.info(f"📥 Fetching initial data for {pokestop_type}...")
                    fetch_initial_data(pokestop_type, type_info)
                
                # Start background update thread
                threading.Thread(target=update_cache, args=(pokestop_type, type_info), daemon=True).start()
                active_types.add(pokestop_type)
                logger.info(f"🛠️ Started cache thread for {pokestop_type}")

    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        logger.debug(f"📖 Loaded cache for {pokestop_type} from {cache_file}")
    except Exception as e:
        logger.error(f"⚠️ Error reading cache for {pokestop_type}: {e}")
        data = {'stops': {location: [] for location in API_ENDPOINTS.keys()}, 'last_updated': 'Unknown'}

    # Sort stops by remaining_time descending (default sort)
    stops = data.get('stops', {location: [] for location in API_ENDPOINTS.keys()})
    for location in stops:
        stops[location] = sorted(stops[location], key=lambda s: s['remaining_time'], reverse=True)

    # Get list of currently active types
    with active_types_lock:
        active_types_list = ', '.join(sorted(active_types))

    return render_template_string(
        HTML_TEMPLATE,
        stops=stops,
        last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        pokestop_type=pokestop_type,
        pokestop_type_display=type_info['display'],
        types=POKESTOP_TYPES.keys(),
        active_types_list=active_types_list or 'None',
        debug=debug
    )

def fetch_initial_data(pokestop_type, type_info):
    """Fetch initial data for a type synchronously (single location for speed)."""
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    cache_file = get_cache_file(pokestop_type)
    dialogue_keywords = type_info.get('dialogue_keywords', [])
    
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
            
            if character_id not in character_ids:
                continue
                
            remaining_time = stop['invasion_end'] - (current_time - time_offset)
            
            if not (MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME):
                continue
            
            grunt_dialogue = stop.get('grunt_dialogue', '').lower()
            
            # Apply same fallback dialogue logic as in fetch_location
            dialogue_match = False
            if pokestop_type == 'ghost' and 'ke' in grunt_dialogue:
                dialogue_match = True
            elif pokestop_type == 'electric' and character_id == 48:
                if any(kw in grunt_dialogue for kw in dialogue_keywords):
                    dialogue_match = True
                else:
                    continue
            elif dialogue_keywords and any(kw in grunt_dialogue for kw in dialogue_keywords):
                dialogue_match = True
            
            stops.append({
                'lat': stop['lat'],
                'lng': stop['lng'],
                'name': stop.get('name', 'Unnamed PokéStop'),
                'remaining_time': remaining_time,
                'character': character_id,
                'type': display_type,
                'gender': gender_map.get(character_id, 'Unknown'),
                'grunt_dialogue': grunt_dialogue,
                'encounter_pokemon_id': stop.get('encounter_pokemon_id', None),
                'dialogue_match': dialogue_match
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
        
        logger.info(f"✅ Initial data fetched for {pokestop_type}: {len(stops)} stops from NYC")
        
    except Exception as e:
        logger.error(f"⚠️ Failed to fetch initial data for {pokestop_type}: {e}")
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