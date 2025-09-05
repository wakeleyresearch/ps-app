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
from threading import RLock, Event
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import logging
from typing import Dict, List, Optional
import signal
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
UPDATE_INTERVAL = 60  # Reduced from 120 to 60 seconds
MIN_REMAINING_TIME = 180
MAX_REMAINING_TIME = 7200
INITIAL_FETCH_TIMEOUT = 10  # Quick initial fetch for new types
MAX_WORKERS = 5
CACHE_DIR = '/app/cache'

# Grunt type configuration
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
    'ghost': {'ids': [47, 48], 'gender': {47: 'Male', 48: 'Female'}, 'display': 'Ghost'}
}

# API endpoints
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php',
    'Sydney': 'https://sydneypogomap.com/pokestop.php'
}

class TypeManager:
    """Thread-safe manager for pokestop types with deadlock prevention."""
    
    def __init__(self):
        # Use RLock (reentrant lock) to prevent deadlocks
        self._lock = RLock()
        self._active_types = set(['fairy'])  # Default type
        self._updater_threads = {}
        self._stop_events = {}
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._shutdown = False
        
        # Ensure cache directory exists
        os.makedirs(CACHE_DIR, exist_ok=True)
    
    def is_type_active(self, pokestop_type: str) -> bool:
        """Check if a type is currently being updated."""
        with self._lock:
            return pokestop_type in self._active_types
    
    def start_type_updater(self, pokestop_type: str, type_info: Dict) -> bool:
        """Start updater for a pokestop type with immediate initial fetch."""
        with self._lock:
            if self._shutdown:
                return False
                
            if pokestop_type in self._active_types:
                logger.info(f"Type {pokestop_type} already active")
                return True
            
            try:
                # Initialize cache
                self._initialize_cache(pokestop_type)
                
                # Create stop event
                stop_event = Event()
                self._stop_events[pokestop_type] = stop_event
                
                # Start immediate fetch for new types
                self._executor.submit(self._immediate_fetch, pokestop_type, type_info)
                
                # Start regular updater thread
                thread = threading.Thread(
                    target=self._update_cache_loop,
                    args=(pokestop_type, type_info, stop_event),
                    daemon=True,
                    name=f"Updater-{pokestop_type}"
                )
                thread.start()
                
                self._updater_threads[pokestop_type] = thread
                self._active_types.add(pokestop_type)
                
                logger.info(f"Started updater for {pokestop_type}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to start updater for {pokestop_type}: {e}")
                # Cleanup on failure
                self._stop_events.pop(pokestop_type, None)
                return False
    
    def stop_type_updater(self, pokestop_type: str) -> bool:
        """Stop updater for a pokestop type."""
        with self._lock:
            if pokestop_type not in self._active_types:
                return True
            
            try:
                # Signal stop
                if pokestop_type in self._stop_events:
                    self._stop_events[pokestop_type].set()
                
                # Clean up
                self._active_types.discard(pokestop_type)
                self._stop_events.pop(pokestop_type, None)
                self._updater_threads.pop(pokestop_type, None)
                
                logger.info(f"Stopped updater for {pokestop_type}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to stop updater for {pokestop_type}: {e}")
                return False
    
    def cleanup_idle_types(self):
        """Clean up idle types (called periodically)."""
        # Get current active types without holding lock during cleanup
        with self._lock:
            types_to_check = list(self._active_types)
        
        # Check each type (without lock to prevent deadlock)
        idle_types = []
        for pokestop_type in types_to_check:
            if self._is_type_idle(pokestop_type):
                idle_types.append(pokestop_type)
        
        # Stop idle types
        for pokestop_type in idle_types:
            self.stop_type_updater(pokestop_type)
    
    def shutdown(self):
        """Shutdown all updaters."""
        with self._lock:
            self._shutdown = True
            
            # Stop all types
            for pokestop_type in list(self._active_types):
                self.stop_type_updater(pokestop_type)
            
            # Shutdown executor
            self._executor.shutdown(wait=True)
    
    def _initialize_cache(self, pokestop_type: str):
        """Initialize cache file for the given type."""
        cache_file = self._get_cache_file(pokestop_type)
        if not os.path.exists(cache_file):
            empty_cache = {
                'stops': {location: [] for location in API_ENDPOINTS.keys()},
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self._write_cache(pokestop_type, empty_cache)
    
    def _immediate_fetch(self, pokestop_type: str, type_info: Dict):
        """Perform immediate fetch for new types."""
        try:
            logger.info(f"Performing immediate fetch for {pokestop_type}")
            data_fetcher = DataFetcher()
            stops_by_location = data_fetcher.fetch_all_locations(pokestop_type, type_info)
            
            cache_data = {
                'stops': stops_by_location,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self._write_cache(pokestop_type, cache_data)
            logger.info(f"Immediate fetch completed for {pokestop_type}")
            
        except Exception as e:
            logger.error(f"Immediate fetch failed for {pokestop_type}: {e}")
    
    def _update_cache_loop(self, pokestop_type: str, type_info: Dict, stop_event: Event):
        """Main update loop for a pokestop type."""
        data_fetcher = DataFetcher()
        
        while not stop_event.is_set() and not self._shutdown:
            try:
                # Wait for either stop signal or update interval
                if stop_event.wait(timeout=UPDATE_INTERVAL):
                    break  # Stop event was set
                
                # Fetch new data
                stops_by_location = data_fetcher.fetch_all_locations(pokestop_type, type_info)
                
                cache_data = {
                    'stops': stops_by_location,
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                self._write_cache(pokestop_type, cache_data)
                logger.info(f"Cache updated for {pokestop_type}")
                
            except Exception as e:
                logger.error(f"Error in update loop for {pokestop_type}: {e}")
                # Continue running even on errors
    
    def _get_cache_file(self, pokestop_type: str) -> str:
        """Return cache file path for the given type."""
        return os.path.join(CACHE_DIR, f'pokestops_{pokestop_type}.json.gz')
    
    def _write_cache(self, pokestop_type: str, data: Dict) -> bool:
        """Write cache with atomic operation and compression."""
        cache_file = self._get_cache_file(pokestop_type)
        temp_file = cache_file + '.tmp'
        
        try:
            with gzip.open(temp_file, 'wt', encoding='utf-8') as f:
                json.dump(data, f, separators=(',', ':'))
            
            os.rename(temp_file, cache_file)
            return True
            
        except Exception as e:
            logger.error(f"Failed to write cache for {pokestop_type}: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
            return False
    
    def read_cache(self, pokestop_type: str) -> Dict:
        """Read cache file for the given type."""
        cache_file = self._get_cache_file(pokestop_type)
        
        try:
            with gzip.open(cache_file, 'rt', encoding='utf-8') as f:
                return json.load(f)
                
        except Exception as e:
            logger.warning(f"Failed to read cache for {pokestop_type}: {e}")
            return {
                'stops': {location: [] for location in API_ENDPOINTS.keys()},
                'last_updated': 'Unknown'
            }
    
    def _is_type_idle(self, pokestop_type: str) -> bool:
        """Check if a type should be considered idle."""
        # Implement your idle detection logic here
        # For example, check last access time, cache age, etc.
        return False  # Placeholder

class DataFetcher:
    """Handles data fetching from API endpoints."""
    
    def __init__(self):
        # Setup proxy if available
        proxy_host = os.environ.get('NORDVPN_PROXY_HOST')
        proxy_user = os.environ.get('NORDVPN_PROXY_USER')
        proxy_pass = os.environ.get('NORDVPN_PROXY_PASS')
        
        if proxy_host and proxy_user and proxy_pass:
            proxy_url = f'socks5://{proxy_user}:{proxy_pass}@{proxy_host}:1080'
            self.proxies = {'http': proxy_url, 'https': proxy_url}
        else:
            self.proxies = None
    
    def fetch_all_locations(self, pokestop_type: str, type_info: Dict) -> Dict[str, List]:
        """Fetch data from all locations concurrently."""
        stops_by_location = {}
        
        with ThreadPoolExecutor(max_workers=len(API_ENDPOINTS)) as executor:
            future_to_location = {
                executor.submit(
                    self.fetch_location_data, 
                    location, url, pokestop_type, type_info
                ): location
                for location, url in API_ENDPOINTS.items()
            }
            
            for future in as_completed(future_to_location, timeout=30):
                location = future_to_location[future]
                try:
                    stops_by_location[location] = future.result()
                except Exception as e:
                    logger.error(f"Failed to fetch data for {location}: {e}")
                    stops_by_location[location] = []
        
        return stops_by_location
    
    def fetch_location_data(self, location: str, url: str, pokestop_type: str, type_info: Dict) -> List[Dict]:
        """Fetch data for a single location."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
            params = {'time': int(time.time() * 1000)}
            
            response = requests.get(
                url, params=params, headers=headers, 
                timeout=INITIAL_FETCH_TIMEOUT, proxies=self.proxies
            )
            response.raise_for_status()
            data = response.json()
            
            return self._process_stops(data, location, pokestop_type, type_info)
            
        except Exception as e:
            logger.error(f"Error fetching data for {location} ({pokestop_type}): {e}")
            return []
    
    def _process_stops(self, data: Dict, location: str, pokestop_type: str, type_info: Dict) -> List[Dict]:
        """Process stops data for a specific type."""
        current_time = time.time()
        meta = data.get('meta', {})
        time_offset = current_time - int(meta.get('time', current_time))
        
        character_ids = type_info['ids']
        gender_map = type_info['gender']
        display_type = type_info['display']
        
        stops = []
        for stop in data.get('invasions', []):
            character_id = stop.get('character')
            grunt_dialogue = stop.get('grunt_dialogue', '').lower()
            
            # Type matching logic
            if self._matches_type(character_id, grunt_dialogue, pokestop_type, character_ids):
                remaining_time = stop['invasion_end'] - (current_time - time_offset)
                
                if MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME:
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
        
        logger.info(f"Fetched {len(stops)} {display_type} ({pokestop_type}) PokéStops for {location}")
        return stops
    
    def _matches_type(self, character_id: int, grunt_dialogue: str, pokestop_type: str, character_ids: List[int]) -> bool:
        """Check if a stop matches the requested type."""
        # Character ID match
        if character_id in character_ids:
            # Special handling for electric type
            if pokestop_type == 'electric':
                return any(kw in grunt_dialogue for kw in ['shock', 'electric', 'volt', 'charge'])
            return True
        
        # Grunt type matching
        if pokestop_type.startswith('grunt') and 'grunt' in grunt_dialogue:
            return True
        
        # Type-specific dialogue matching
        if not pokestop_type.startswith('grunt'):
            if pokestop_type.lower() in grunt_dialogue:
                return True
            if pokestop_type == 'ghost' and 'ke...ke...' in grunt_dialogue:
                return True
        
        return False

# Global type manager instance
type_manager = TypeManager()

# Initialize default type
type_manager.start_type_updater('fairy', POKESTOP_TYPES['fairy'])

# Graceful shutdown handling
def signal_handler(signum, frame):
    logger.info("Shutting down gracefully...")
    type_manager.shutdown()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# HTML template (same as before)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ pokestop_type.capitalize() }}-Type PokéStops</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
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
        .loading { color: #ff9900; font-style: italic; }
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
            const R = 6371;
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
            points.sort((a, b) => b.remaining_time - a.remaining_time);
            let ordered = [points.shift()];
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
                let html = `${stop.type} (${stop.gender}) ${stop.name} (<a href="https://maps.google.com/?q=${stop.lat},${stop.lng}">${stop.lat}, ${stop.lng}</a>) - ${Math.floor(stop.remaining_time / 60)} min ${stop.remaining_time % 60} sec remaining`;
                if (isDebug) {
                    html += `<span class="debug">(Character: ${stop.character}, Dialogue: ${stop.grunt_dialogue || 'N/A'}, Encounter ID: ${stop.encounter_pokemon_id || 'N/A'})</span>`;
                }
                li.innerHTML = html;
                ul.appendChild(li);
            });
        }
        
        function toggleSort(location) {
            sortMode[location] = sortMode[location] === 'nearest' ? 'time' : 'nearest';
            let button = document.getElementById('sort-btn-' + location);
            button.textContent = sortMode[location] === 'nearest' ? 'Sort by Time Remaining' : 'Sort by Nearest Neighbor';
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
    <h1>{{ pokestop_type.capitalize() }}-Type PokéStops</h1>
    <p>Last updated: {{ last_updated }}</p>
    <p>Updates every minute. Only PokéStops with more than 3 minutes remaining are shown.</p>
    <p>Switch type:
        {% for type in types %}
            <a href="?type={{ type }}{% if debug %}&debug=true{% endif %}">{{ type.capitalize() }}</a>{% if not loop.last %}, {% endif %}
        {% endfor %}
    </p>
    <p>
        <a href="/download_gpx?type={{ pokestop_type }}" target="_blank">Download GPX (over 10 min remaining)</a>
    </p>
    {% for location, location_stops in stops.items() %}
        <h2>{{ location }}</h2>
        <button id="sort-btn-{{ location }}" onclick="toggleSort('{{ location }}')">Sort by Nearest Neighbor</button>
        {% if location_stops %}
            <ul id="stops-list-{{ location }}">
                {% for stop in location_stops %}
                    <li>{{ stop.type }} ({{ stop.gender }}) {{ stop.name }} (<a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}">{{ stop.lat }}, {{ stop.lng }}</a>) - {{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining
                        {% if debug %}
                            <span class="debug">(Character: {{ stop.character }}, Dialogue: {{ stop.grunt_dialogue|default('N/A') }}, Encounter ID: {{ stop.encounter_pokemon_id|default('N/A') }})</span>
                        {% endif %}
                    </li>
                {% endfor %}
            </ul>
        {% else %}
            <p class="no-stops">{% if last_updated == 'Unknown' %}<span class="loading">Loading {{ pokestop_type.capitalize() }}-type data...</span>{% else %}No {{ pokestop_type.capitalize() }}-type PokéStops found in {{ location }}.{% endif %}</p>
        {% endif %}
    {% endfor %}
</body>
</html>
"""

@app.route('/')
def get_pokestops():
    """Main route for displaying pokestops."""
    pokestop_type = request.args.get('type', 'fairy').lower()
    debug = request.args.get('debug', 'false').lower() == 'true'
    
    if pokestop_type not in POKESTOP_TYPES:
        pokestop_type = 'fairy'
    
    type_info = POKESTOP_TYPES[pokestop_type]
    
    # Start updater if not active
    if not type_manager.is_type_active(pokestop_type):
        type_manager.start_type_updater(pokestop_type, type_info)
    
    # Read cache
    try:
        data = type_manager.read_cache(pokestop_type)
        logger.info(f"Loaded cache for {pokestop_type}")
    except Exception as e:
        logger.error(f"Error reading cache for {pokestop_type}: {e}")
        data = {
            'stops': {location: [] for location in API_ENDPOINTS.keys()},
            'last_updated': 'Unknown'
        }
    
    # Sort stops by remaining_time descending
    stops = data.get('stops', {location: [] for location in API_ENDPOINTS.keys()})
    for location in stops:
        stops[location] = sorted(stops[location], key=lambda s: s['remaining_time'], reverse=True)
    
    try:
        return render_template_string(
            HTML_TEMPLATE,
            stops=stops,
            last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            pokestop_type=pokestop_type,
            types=POKESTOP_TYPES.keys(),
            debug=debug
        )
    except Exception as e:
        logger.error(f"Render failed for {pokestop_type}: {e}")
        return render_template_string(
            HTML_TEMPLATE,
            stops={location: [] for location in API_ENDPOINTS.keys()},
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            pokestop_type=pokestop_type,
            types=POKESTOP_TYPES.keys(),
            debug=debug
        ), 500

@app.route('/download_gpx')
def download_gpx():
    """Download GPX file for stops with >10 minutes remaining."""
    pokestop_type = request.args.get('type', 'fairy').lower()
    
    if pokestop_type not in POKESTOP_TYPES:
        pokestop_type = 'fairy'
    
    try:
        data = type_manager.read_cache(pokestop_type)
        
        # Filter stops with remaining_time > 600 seconds (10 minutes)
        filtered_stops = []
        for location, stops in data['stops'].items():
            for stop in stops:
                if stop['remaining_time'] > 600:
                    filtered_stops.append(stop)
        
        # Generate GPX
        gpx = ET.Element('gpx', version="1.1", creator="Pokestops App")
        for stop in filtered_stops:
            wpt = ET.SubElement(gpx, 'wpt', lat=str(stop['lat']), lon=str(stop['lng']))
            name = ET.SubElement(wpt, 'name')
            name.text = stop['name']
        
        gpx_str = ET.tostring(gpx, encoding='unicode')
        
        return send_file(
            BytesIO(gpx_str.encode()),
            mimetype='application/gpx+xml',
            as_attachment=True,
            download_name=f'{pokestop_type}_pokestops.gpx'
        )
        
    except Exception as e:
        logger.error(f"Error generating GPX for {pokestop_type}: {e}")
        return "Error generating GPX file", 500

@app.route('/debug_api')
def debug_api():
    """Debug endpoint to inspect raw API data."""
    location = request.args.get('location', 'London')
    url = API_ENDPOINTS.get(location, API_ENDPOINTS['London'])
    
    try:
        response = requests.get(url, params={'time': int(time.time() * 1000)}, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/health')
def health_check():
    """Health check endpoint."""
    try:
        # Check if at least one type is active
        active_count = len(type_manager._active_types)
        
        return {
            'status': 'healthy',
            'active_types': active_count,
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {'status': 'unhealthy', 'error': str(e)}, 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
