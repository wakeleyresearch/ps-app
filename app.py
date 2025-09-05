from flask import Flask, render_template_string, request, send_file
from io import BytesIO
import xml.etree.ElementTree as ET
import requests
from datetime import datetime
import time
import threading
import json
import os
import gzip
import hashlib
import logging
from threading import Lock, RLock
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Set
from collections import OrderedDict

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cache update interval (seconds)
UPDATE_INTERVAL = 120
# Minimum remaining time for PokéStops (seconds)
MIN_REMAINING_TIME = 180
# Maximum remaining time for PokéStops (seconds)
MAX_REMAINING_TIME = 7200

# FIXED: Separate water male/female types and ensure consistent ordering
POKESTOP_TYPES = OrderedDict([
    # Regular types with separate male/female pages
    ('bugfemale', {'ids': [6], 'gender': {6: 'Female'}, 'display': 'Bug', 'category': 'regular'}),
    ('bugmale', {'ids': [7], 'gender': {7: 'Male'}, 'display': 'Bug', 'category': 'regular'}),
    ('darkfemale', {'ids': [10], 'gender': {10: 'Female'}, 'display': 'Dark', 'category': 'regular'}),
    ('darkmale', {'ids': [11], 'gender': {11: 'Male'}, 'display': 'Dark', 'category': 'regular'}),
    ('dragonfemale', {'ids': [12], 'gender': {12: 'Female'}, 'display': 'Dragon', 'category': 'regular'}),
    ('dragonmale', {'ids': [13], 'gender': {13: 'Male'}, 'display': 'Dragon', 'category': 'regular'}),
    ('electricfemale', {'ids': [48], 'gender': {48: 'Female'}, 'display': 'Electric', 'category': 'regular'}),
    ('electricmale', {'ids': [49], 'gender': {49: 'Male'}, 'display': 'Electric', 'category': 'regular'}),
    ('fairyfemale', {'ids': [14], 'gender': {14: 'Female'}, 'display': 'Fairy', 'category': 'regular'}),
    ('fairymale', {'ids': [15], 'gender': {15: 'Male'}, 'display': 'Fairy', 'category': 'regular'}),
    ('fightingfemale', {'ids': [16], 'gender': {16: 'Female'}, 'display': 'Fighting', 'category': 'regular'}),
    ('fightingmale', {'ids': [17], 'gender': {17: 'Male'}, 'display': 'Fighting', 'category': 'regular'}),
    ('firefemale', {'ids': [18], 'gender': {18: 'Female'}, 'display': 'Fire', 'category': 'regular'}),
    ('firemale', {'ids': [19], 'gender': {19: 'Male'}, 'display': 'Fire', 'category': 'regular'}),
    ('flyingfemale', {'ids': [20], 'gender': {20: 'Female'}, 'display': 'Flying', 'category': 'regular'}),
    ('flyingmale', {'ids': [21], 'gender': {21: 'Male'}, 'display': 'Flying', 'category': 'regular'}),
    ('ghostmale', {'ids': [47], 'gender': {47: 'Male'}, 'display': 'Ghost', 'category': 'regular'}),
    ('ghostfemale', {'ids': [48], 'gender': {48: 'Female'}, 'display': 'Ghost', 'category': 'regular'}),
    ('grassfemale', {'ids': [22], 'gender': {22: 'Female'}, 'display': 'Grass', 'category': 'regular'}),
    ('grassmale', {'ids': [23], 'gender': {23: 'Male'}, 'display': 'Grass', 'category': 'regular'}),
    ('groundfemale', {'ids': [24], 'gender': {24: 'Female'}, 'display': 'Ground', 'category': 'regular'}),
    ('groundmale', {'ids': [25], 'gender': {25: 'Male'}, 'display': 'Ground', 'category': 'regular'}),
    ('icefemale', {'ids': [26], 'gender': {26: 'Female'}, 'display': 'Ice', 'category': 'regular'}),
    ('icemale', {'ids': [27], 'gender': {27: 'Male'}, 'display': 'Ice', 'category': 'regular'}),
    ('metalfemale', {'ids': [28], 'gender': {28: 'Female'}, 'display': 'Metal', 'category': 'regular'}),
    ('metalmale', {'ids': [29], 'gender': {29: 'Male'}, 'display': 'Metal', 'category': 'regular'}),
    ('normalfemale', {'ids': [30], 'gender': {30: 'Female'}, 'display': 'Normal', 'category': 'regular'}),
    ('normalmale', {'ids': [31], 'gender': {31: 'Male'}, 'display': 'Normal', 'category': 'regular'}),
    ('poisonfemale', {'ids': [32], 'gender': {32: 'Female'}, 'display': 'Poison', 'category': 'regular'}),
    ('poisonmale', {'ids': [33], 'gender': {33: 'Male'}, 'display': 'Poison', 'category': 'regular'}),
    ('psychicfemale', {'ids': [34], 'gender': {34: 'Female'}, 'display': 'Psychic', 'category': 'regular'}),
    ('psychicmale', {'ids': [35], 'gender': {35: 'Male'}, 'display': 'Psychic', 'category': 'regular'}),
    ('rockfemale', {'ids': [36], 'gender': {36: 'Female'}, 'display': 'Rock', 'category': 'regular'}),
    ('rockmale', {'ids': [37], 'gender': {37: 'Male'}, 'display': 'Rock', 'category': 'regular'}),
    # FIXED: Water types split into separate male/female
    ('waterfemale', {'ids': [38], 'gender': {38: 'Female'}, 'display': 'Water', 'category': 'regular'}),
    ('watermale', {'ids': [39], 'gender': {39: 'Male'}, 'display': 'Water', 'category': 'regular'}),
    
    # Grunt types
    ('gruntmale', {'ids': [4], 'display': 'Grunt', 'gender_display': 'Male', 'category': 'grunt'}),
    ('gruntfemale', {'ids': [5], 'display': 'Grunt', 'gender_display': 'Female', 'category': 'grunt'})
])

# FIXED: Ordered API endpoints to maintain consistent location order
API_ENDPOINTS = OrderedDict([
    ('NYC', 'https://nycpokemap.com/pokestop.php'),
    ('Vancouver', 'https://vanpokemap.com/pokestop.php'),
    ('London', 'https://londonpogomap.com/pokestop.php'),
    ('Singapore', 'https://sgpokemap.com/pokestop.php'),
    ('Sydney', 'https://sydneypogomap.com/pokestop.php')
])

class ImprovedCacheManager:
    """Thread-safe cache manager with compression and proper persistence."""
    
    def __init__(self, cache_dir: str = '/app/cache'):
        self.cache_dir = cache_dir
        self.cache_lock = RLock()
        self._memory_cache = {}
        self._ensure_cache_dir()
    
    def _ensure_cache_dir(self):
        """Ensure cache directory exists."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            logger.info(f"Cache directory ready: {self.cache_dir}")
        except Exception as e:
            logger.error(f"Failed to create cache directory: {e}")
            raise
    
    def get_cache_file(self, pokestop_type: str) -> str:
        """Return cache file path for the given type."""
        return os.path.join(self.cache_dir, f'pokestops_{pokestop_type}.json.gz')
    
    def read_cache(self, pokestop_type: str) -> Dict:
        """Read cache with proper fallback handling."""
        with self.cache_lock:
            # Try memory cache first
            if pokestop_type in self._memory_cache:
                logger.debug(f"Cache hit (memory) for {pokestop_type}")
                return self._memory_cache[pokestop_type].copy()
            
            # Try file cache
            cache_file = self.get_cache_file(pokestop_type)
            try:
                if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                    with gzip.open(cache_file, 'rt', encoding='utf-8') as f:
                        data = json.load(f)
                        # Validate cache structure
                        if 'stops' in data and isinstance(data['stops'], dict):
                            self._memory_cache[pokestop_type] = data
                            logger.info(f"Cache loaded from file for {pokestop_type}")
                            return data.copy()
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read cache for {pokestop_type}: {e}")
            
            # Return empty cache structure
            logger.info(f"No valid cache found for {pokestop_type}, returning empty cache")
            return self._get_empty_cache()
    
    def write_cache(self, pokestop_type: str, data: Dict) -> bool:
        """Write cache with atomic operation and compression."""
        with self.cache_lock:
            try:
                # Validate data structure
                if not data or 'stops' not in data:
                    logger.warning(f"Invalid data structure for {pokestop_type}, skipping write")
                    return False
                
                # Update memory cache
                self._memory_cache[pokestop_type] = data.copy()
                
                # Write to file atomically
                cache_file = self.get_cache_file(pokestop_type)
                temp_file = cache_file + '.tmp'
                
                with gzip.open(temp_file, 'wt', encoding='utf-8') as f:
                    json.dump(data, f, separators=(',', ':'))
                
                # Atomic move
                os.rename(temp_file, cache_file)
                logger.info(f"Cache successfully written for {pokestop_type}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to write cache for {pokestop_type}: {e}")
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                return False
    
    def _get_empty_cache(self) -> Dict:
        """Return empty cache structure with proper ordering."""
        return {
            'stops': OrderedDict([(location, []) for location in API_ENDPOINTS.keys()]),
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'hash': hashlib.md5(b'empty').hexdigest()
        }

class RobustScraper:
    """Scraper with improved error handling and retry logic."""
    
    def __init__(self, pokestop_type: str, type_info: Dict):
        self.pokestop_type = pokestop_type
        self.type_info = type_info
        self.session = self._create_session()
        self.character_ids = type_info['ids']
        
        # Handle gender mapping correctly for both regular and grunt types
        if 'gender' in type_info:
            self.gender_map = type_info['gender']
        else:
            # For grunt types, use the gender_display
            self.gender_map = {id: type_info['gender_display'] for id in type_info['ids']}
        
        self.display_type = type_info['display']
        logger.info(f"Initialized scraper for {self.display_type} ({pokestop_type}) - Character IDs: {self.character_ids}")
    
    def _create_session(self) -> requests.Session:
        """Create session with retry strategy and proxy support."""
        session = requests.Session()
        
        # Retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Configure proxy if available
        proxy_host = os.environ.get('NORDVPN_PROXY_HOST')
        proxy_user = os.environ.get('NORDVPN_PROXY_USER')
        proxy_pass = os.environ.get('NORDVPN_PROXY_PASS')
        
        if proxy_host and proxy_user and proxy_pass:
            proxy_url = f'socks5://{proxy_user}:{proxy_pass}@{proxy_host}:1080'
            session.proxies = {'http': proxy_url, 'https': proxy_url}
        
        return session
    
    def fetch_all_locations(self) -> OrderedDict:
        """Fetch data from all locations maintaining order."""
        stops_by_location = OrderedDict()
        
        # Process locations in order
        for location, url in API_ENDPOINTS.items():
            stops_by_location[location] = self.fetch_location_data(location, url)
            time.sleep(2)  # Rate limiting between API calls
        
        return stops_by_location
    
    def fetch_location_data(self, location: str, url: str) -> List[Dict]:
        """Fetch data for a single location with improved error handling."""
        current_time = time.time()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
        params = {'time': int(current_time * 1000)}
        
        for attempt in range(3):
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=15)
                response.raise_for_status()
                
                data = response.json()
                meta = data.get('meta', {})
                time_offset = current_time - int(meta.get('time', current_time))
                
                stops = self._process_stops(data.get('invasions', []), current_time, time_offset, location)
                logger.info(f"✅ Fetched {len(stops)} {self.display_type} ({self.pokestop_type}) PokéStops for {location}")
                return stops
                
            except requests.exceptions.ConnectionError as e:
                if attempt < 2:
                    logger.warning(f"Attempt {attempt + 1}/3 failed for {location}: {e}")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"Connection failed for {location} after 3 attempts")
                    return []
            except Exception as e:
                logger.error(f"Unexpected error fetching {location}: {e}")
                return []
        
        return []
    
    def _process_stops(self, invasions: List, current_time: float, time_offset: float, location: str) -> List[Dict]:
        """Process invasion data into stops list."""
        stops = []
        
        for stop in invasions:
            character_id = stop.get('character')
            grunt_dialogue = stop.get('grunt_dialogue', '').lower()
            
            is_match = self._is_matching_stop(character_id, grunt_dialogue)
            remaining_time = stop['invasion_end'] - (current_time - time_offset)
            
            if is_match and MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME:
                stops.append({
                    'lat': stop['lat'],
                    'lng': stop['lng'],
                    'name': stop.get('name', f'Unnamed PokéStop ({location})'),
                    'remaining_time': remaining_time,
                    'character': character_id,
                    'type': self.display_type,
                    'gender': self.gender_map.get(character_id, 'Unknown'),
                    'grunt_dialogue': grunt_dialogue,
                    'encounter_pokemon_id': stop.get('encounter_pokemon_id', None)
                })
        
        return stops
    
    def _is_matching_stop(self, character_id: int, grunt_dialogue: str) -> bool:
        """Determine if a stop matches this scraper's criteria."""
        if character_id in self.character_ids:
            return True
        
        if self.pokestop_type.startswith('grunt') and 'grunt' in grunt_dialogue:
            return True
        
        if not self.pokestop_type.startswith('grunt'):
            base_type = self.pokestop_type.replace('male', '').replace('female', '')
            if base_type.lower() in grunt_dialogue:
                return True
            if base_type == 'ghost' and 'ke...ke...' in grunt_dialogue:
                return True
            if base_type == 'electric' and any(kw in grunt_dialogue for kw in ['shock', 'electric', 'volt', 'charge']):
                return True
        
        return False

class PersistentTypeManager:
    """Manage active scraper threads with proper cache persistence."""
    
    def __init__(self):
        self.active_types: Set[str] = set()
        self.type_lock = Lock()
        self.cache_manager = ImprovedCacheManager()
        self.initialization_complete = False
    
    def initialize_all_types_sequential(self):
        """Initialize all types sequentially to ensure proper cache creation."""
        logger.info("Starting sequential initialization of all PokéStop types...")
        
        # Priority order: most common types first
        priority_types = ['fairyfemale', 'fairymale', 'firefemale', 'firemale', 'waterfemale', 'watermale']
        remaining_types = [t for t in POKESTOP_TYPES.keys() if t not in priority_types]
        
        all_types = priority_types + remaining_types
        
        for pokestop_type in all_types:
            if pokestop_type in POKESTOP_TYPES:
                type_info = POKESTOP_TYPES[pokestop_type]
                self._initialize_single_type(pokestop_type, type_info)
                time.sleep(1)  # Small delay to prevent API overload
        
        self.initialization_complete = True
        logger.info("✅ Sequential initialization complete for all types")
    
    def _initialize_single_type(self, pokestop_type: str, type_info: Dict):
        """Initialize a single type with proper cache creation."""
        if self.activate_type(pokestop_type):
            # Create initial empty cache
            empty_cache = self.cache_manager._get_empty_cache()
            self.cache_manager.write_cache(pokestop_type, empty_cache)
            
            # Start background thread
            thread = threading.Thread(
                target=self.update_cache_for_type,
                args=(pokestop_type, type_info),
                daemon=True,
                name=f"cache-{pokestop_type}"
            )
            thread.start()
            logger.info(f"🛠️ Started cache thread for {pokestop_type}")
    
    def activate_type(self, pokestop_type: str) -> bool:
        """Activate a type for scraping. Returns True if newly activated."""
        with self.type_lock:
            if pokestop_type not in self.active_types:
                self.active_types.add(pokestop_type)
                return True
            return False
    
    def is_type_active(self, pokestop_type: str) -> bool:
        """Check if a type is already being scraped."""
        with self.type_lock:
            return pokestop_type in self.active_types
    
    def update_cache_for_type(self, pokestop_type: str, type_info: Dict):
        """Update cache for a specific type with proper persistence."""
        scraper = RobustScraper(pokestop_type, type_info)
        
        while True:
            try:
                logger.info(f"Starting cache update for {pokestop_type}")
                
                # Fetch data maintaining location order
                stops_by_location = scraper.fetch_all_locations()
                
                # Prepare cache data with proper structure
                new_data = {
                    'stops': stops_by_location,
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                # Write to cache
                if self.cache_manager.write_cache(pokestop_type, new_data):
                    total_stops = sum(len(stops) for stops in stops_by_location.values())
                    logger.info(f"✅ Cache updated for {pokestop_type}: {total_stops} total stops")
                else:
                    logger.error(f"Failed to write cache for {pokestop_type}")
                
            except Exception as e:
                logger.error(f"Error updating cache for {pokestop_type}: {e}")
            
            time.sleep(UPDATE_INTERVAL)

# Global type manager
type_manager = PersistentTypeManager()

# MOBILE-OPTIMIZED HTML template with fixed water type support
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ display_name }} PokéStops</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <meta http-equiv="refresh" content="120">
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; 
            margin: 0; 
            padding: 15px; 
            font-size: 16px;
            line-height: 1.4;
            background-color: #f8f9fa;
        }
        
        h1 { 
            color: #333; 
            font-size: 1.5em; 
            margin: 0 0 15px 0; 
            text-align: center;
        }
        
        h2 { 
            color: #555; 
            font-size: 1.3em; 
            margin: 25px 0 15px 0; 
            border-bottom: 2px solid #007acc;
            padding-bottom: 5px;
        }
        
        h3 { 
            color: #666; 
            font-size: 1.1em; 
            margin: 20px 0 10px 0; 
        }
        
        .info-text {
            font-size: 0.9em;
            color: #666;
            margin-bottom: 20px;
            text-align: center;
        }
        
        .type-group { 
            margin-bottom: 25px; 
            background: #fff;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .type-links { 
            display: flex; 
            flex-wrap: wrap; 
            gap: 8px; 
            margin-bottom: 10px; 
        }
        
        .type-links a { 
            padding: 8px 12px; 
            background: #e9ecef; 
            color: #495057;
            text-decoration: none;
            border-radius: 6px; 
            font-size: 0.9em;
            white-space: nowrap;
            transition: all 0.2s;
            min-height: 36px;
            display: flex;
            align-items: center;
            border: 1px solid #dee2e6;
        }
        
        .type-links a:hover { 
            background: #dee2e6; 
            transform: translateY(-1px);
        }
        
        .type-links a.active { 
            background: #007acc; 
            color: white; 
            font-weight: 500;
            border-color: #007acc;
        }
        
        .controls {
            margin: 20px 0;
            padding: 15px;
            background: #fff;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .sort-btn {
            background: #28a745;
            color: white;
            border: none;
            padding: 12px 15px;
            border-radius: 6px;
            font-size: 0.9em;
            cursor: pointer;
            width: 100%;
            margin-bottom: 10px;
            transition: background 0.2s;
            min-height: 44px;
        }
        
        .sort-btn:hover {
            background: #218838;
        }
        
        .download-link {
            display: inline-block;
            background: #17a2b8;
            color: white;
            text-decoration: none;
            padding: 12px 15px;
            border-radius: 6px;
            font-size: 0.9em;
            text-align: center;
            width: 100%;
            margin-top: 10px;
            min-height: 44px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .download-link:hover {
            background: #138496;
        }
        
        ul { 
            list-style-type: none; 
            padding: 0; 
            margin: 0;
        }
        
        li { 
            margin: 8px 0; 
            padding: 12px;
            background: #fff;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            font-size: 0.9em;
            line-height: 1.5;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        
        li a { 
            color: #007acc; 
            text-decoration: none; 
            word-break: break-all;
        }
        
        li a:hover { 
            text-decoration: underline; 
        }
        
        .no-stops { 
            color: #6c757d; 
            font-style: italic;
            text-align: center;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 6px;
            border: 1px solid #dee2e6;
        }
        
        .debug { 
            font-size: 0.8em; 
            color: #6c757d; 
            display: block;
            margin-top: 5px;
            font-family: monospace;
        }
        
        .stop-header {
            font-weight: 500;
            color: #333;
        }
        
        .time-remaining {
            color: #28a745;
            font-weight: 500;
        }
        
        /* Mobile optimizations */
        @media (max-width: 480px) {
            body { 
                padding: 10px; 
                font-size: 14px;
            }
            
            h1 { 
                font-size: 1.4em; 
            }
            
            h2 { 
                font-size: 1.2em; 
            }
            
            .type-links a { 
                font-size: 0.8em; 
                padding: 6px 10px;
                min-height: 32px;
            }
            
            li { 
                font-size: 0.85em; 
                padding: 10px;
            }
            
            .debug { 
                font-size: 0.75em; 
            }
            
            .sort-btn, .download-link {
                min-height: 40px;
                font-size: 0.85em;
            }
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
            if (!ul) return;
            ul.innerHTML = '';
            stops.forEach(stop => {
                let li = document.createElement('li');
                let minutes = Math.floor(stop.remaining_time / 60);
                let seconds = Math.floor(stop.remaining_time % 60);
                
                let html = `<div class="stop-header">${stop.type} (${stop.gender}) ${stop.name}</div>`;
                html += `<div><a href="https://maps.google.com/?q=${stop.lat},${stop.lng}">${stop.lat}, ${stop.lng}</a></div>`;
                html += `<div class="time-remaining">${minutes} min ${seconds} sec remaining</div>`;
                
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
            if (button) {
                button.textContent = sortMode[location] === 'nearest' ? 'Sort by Time Remaining' : 'Sort by Nearest Neighbor';
                let stops = [...stopsData[location]];
                if (sortMode[location] === 'nearest') {
                    stops = nearestNeighbor(stops);
                } else {
                    stops.sort((a, b) => b.remaining_time - a.remaining_time);
                }
                renderStops(location, stops);
            }
        }
    </script>
</head>
<body>
    <h1>{{ display_name }} PokéStops</h1>
    
    <div class="info-text">
        <div>Last updated: {{ last_updated }}</div>
        <div>Updates every 2 minutes. Only PokéStops with more than 3 minutes remaining are shown.</div>
    </div>
    
    <div class="type-group">
        <h3>Regular Types:</h3>
        <div class="type-links">
            {% for type_key, info in regular_types.items() %}
                <a href="?type={{ type_key }}female{% if debug %}&debug=true{% endif %}"
                   {% if pokestop_type == type_key + 'female' %}class="active"{% endif %}>{{ info.display }} ♀</a>
                <a href="?type={{ type_key }}male{% if debug %}&debug=true{% endif %}"
                   {% if pokestop_type == type_key + 'male' %}class="active"{% endif %}>{{ info.display }} ♂</a>
            {% endfor %}
        </div>
    </div>
    
    <div class="type-group">
        <h3>Grunt Types:</h3>
        <div class="type-links">
            {% for type_key, info in grunt_types.items() %}
                <a href="?type={{ type_key }}{% if debug %}&debug=true{% endif %}"
                   {% if pokestop_type == type_key %}class="active"{% endif %}>{{ info.display }} ({{ info.gender_display }})</a>
            {% endfor %}
        </div>
    </div>
    
    <div class="controls">
        <a href="/download_gpx?type={{ pokestop_type }}" class="download-link">Download GPX (over 10 min remaining)</a>
    </div>
    
    {% for location, location_stops in stops.items() %}
        <h2>{{ location }}</h2>
        <div class="controls">
            <button id="sort-btn-{{ location }}" class="sort-btn" onclick="toggleSort('{{ location }}')">Sort by Nearest Neighbor</button>
        </div>
        {% if location_stops %}
            <ul id="stops-list-{{ location }}">
                {% for stop in location_stops %}
                    <li>
                        <div class="stop-header">{{ stop.type }} ({{ stop.gender }}) {{ stop.name }}</div>
                        <div><a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}">{{ stop.lat }}, {{ stop.lng }}</a></div>
                        <div class="time-remaining">{{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining</div>
                        {% if debug %}
                            <div class="debug">Character: {{ stop.character }}, Dialogue: {{ stop.grunt_dialogue|default('N/A') }}, Encounter ID: {{ stop.encounter_pokemon_id|default('N/A') }}</div>
                        {% endif %}
                    </li>
                {% endfor %}
            </ul>
        {% else %}
            <div class="no-stops">No {{ display_name }}-type PokéStops found in {{ location }}.</div>
        {% endif %}
    {% endfor %}
</body>
</html>
"""

def get_type_groups():
    """Get organized type groups for the UI."""
    regular_types = OrderedDict()
    grunt_types = OrderedDict()
    
    # Track base types to create grouped UI
    seen_base_types = set()
    
    for type_key, info in POKESTOP_TYPES.items():
        if info['category'] == 'grunt':
            grunt_types[type_key] = {
                'display': info['display'],
                'gender_display': info['gender_display']
            }
        else:
            # Extract base type (remove male/female suffix)
            base_type = type_key.replace('male', '').replace('female', '')
            if base_type not in seen_base_types:
                regular_types[base_type] = {
                    'display': info['display']
                }
                seen_base_types.add(base_type)
    
    return regular_types, grunt_types

@app.route('/download_gpx')
def download_gpx():
    pokestop_type = request.args.get('type', 'fairyfemale').lower()
    if pokestop_type not in POKESTOP_TYPES:
        pokestop_type = 'fairyfemale'
    
    try:
        data = type_manager.cache_manager.read_cache(pokestop_type)
    except Exception as e:
        logger.warning(f"Error reading cache for {pokestop_type}: {e}")
        data = {'stops': OrderedDict([(location, []) for location in API_ENDPOINTS.keys()]), 'last_updated': 'Unknown'}
    
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
    
    return send_file(
        BytesIO(gpx_str.encode()),
        mimetype='application/gpx+xml',
        as_attachment=True,
        download_name='pokestops.gpx'
    )

@app.route('/health')
def health_check():
    """Health check endpoint to monitor cache status."""
    status = {}
    for ptype in POKESTOP_TYPES.keys():
        cache_file = type_manager.cache_manager.get_cache_file(ptype)
        status[ptype] = {
            'cache_exists': os.path.exists(cache_file),
            'file_size': os.path.getsize(cache_file) if os.path.exists(cache_file) else 0,
            'modified': datetime.fromtimestamp(os.path.getmtime(cache_file)).isoformat() if os.path.exists(cache_file) else None,
            'thread_active': type_manager.is_type_active(ptype)
        }
    return status

@app.route('/')
def get_pokestops():
    pokestop_type = request.args.get('type', 'fairyfemale').lower()
    debug = request.args.get('debug', 'false').lower() == 'true'
    
    # Handle legacy types and ensure valid type
    if pokestop_type not in POKESTOP_TYPES:
        # Try to find a close match
        for key in POKESTOP_TYPES.keys():
            if key.startswith(pokestop_type.replace('male', '').replace('female', '')):
                pokestop_type = key
                break
        else:
            pokestop_type = 'fairyfemale'
    
    type_info = POKESTOP_TYPES[pokestop_type]
    
    # Ensure this type is being cached
    if not type_manager.is_type_active(pokestop_type):
        type_manager._initialize_single_type(pokestop_type, type_info)
    
    try:
        data = type_manager.cache_manager.read_cache(pokestop_type)
        logger.info(f"Successfully loaded cache for {pokestop_type}")
    except Exception as e:
        logger.warning(f"Error reading cache for {pokestop_type}: {e}")
        data = {
            'stops': OrderedDict([(location, []) for location in API_ENDPOINTS.keys()]), 
            'last_updated': 'Unknown'
        }
    
    # Ensure proper ordering and sorting
    stops = data.get('stops', OrderedDict())
    if not isinstance(stops, OrderedDict):
        stops = OrderedDict([(location, stops.get(location, [])) for location in API_ENDPOINTS.keys()])
    
    # Sort stops by remaining_time descending within each location
    for location in stops:
        if stops[location]:
            stops[location] = sorted(stops[location], key=lambda s: s.get('remaining_time', 0), reverse=True)
    
    # Get organized type groups
    regular_types, grunt_types = get_type_groups()
    
    # Determine display name
    if pokestop_type.endswith('male'):
        if pokestop_type.startswith('grunt'):
            display_name = "Grunt (Male)"
        else:
            display_name = f"{type_info['display']} (Male)"
    elif pokestop_type.endswith('female'):
        if pokestop_type.startswith('grunt'):
            display_name = "Grunt (Female)"
        else:
            display_name = f"{type_info['display']} (Female)"
    else:
        display_name = type_info['display']
    
    try:
        return render_template_string(
            HTML_TEMPLATE,
            stops=stops,
            last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            pokestop_type=pokestop_type,
            display_name=display_name,
            regular_types=regular_types,
            grunt_types=grunt_types,
            debug=debug
        )
    except Exception as e:
        logger.error(f"Render failed for {pokestop_type}: {e}")
        # Return empty page with proper structure
        empty_stops = OrderedDict([(location, []) for location in API_ENDPOINTS.keys()])
        return render_template_string(
            HTML_TEMPLATE,
            stops=empty_stops,
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            pokestop_type=pokestop_type,
            display_name=display_name,
            regular_types=regular_types,
            grunt_types=grunt_types,
            debug=debug
        )

if __name__ == '__main__':
    # Sequential initialization to ensure proper cache persistence
    initialization_thread = threading.Thread(
        target=type_manager.initialize_all_types_sequential,
        daemon=True,
        name="sequential-initialization"
    )
    initialization_thread.start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
