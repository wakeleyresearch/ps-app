# app.py
from flask import Flask, render_template_string, request
import requests
from datetime import datetime
import time
import threading
import json
import os
import logging
from threading import RLock, Event
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
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
UPDATE_INTERVAL = 60
MIN_REMAINING_TIME = 180
MAX_REMAINING_TIME = 7200
INITIAL_FETCH_TIMEOUT = 10
MAX_WORKERS = 5
CACHE_DIR = '/app/cache'

# Grunt type configuration - imported from config but kept here for reference
POKESTOP_TYPES = {
    'gruntmale': {'ids': [4], 'gender': {4: 'Male'}, 'display': 'Grunt', 'button_label': 'Grunt (Male)'},
    'gruntfemale': {'ids': [5], 'gender': {5: 'Female'}, 'display': 'Grunt', 'button_label': 'Grunt (Female)'},
    'bug': {'ids': [6, 7], 'gender': {7: 'Male', 6: 'Female'}, 'display': 'Bug', 'button_label': 'Bug'},
    'dark': {'ids': [10, 11], 'gender': {11: 'Male', 10: 'Female'}, 'display': 'Dark', 'button_label': 'Dark'},
    'dragon': {'ids': [12, 13], 'gender': {13: 'Male', 12: 'Female'}, 'display': 'Dragon', 'button_label': 'Dragon'},
    'fairy': {'ids': [14, 15], 'gender': {15: 'Male', 14: 'Female'}, 'display': 'Fairy', 'button_label': 'Fairy'},
    'fighting': {'ids': [16, 17], 'gender': {17: 'Male', 16: 'Female'}, 'display': 'Fighting', 'button_label': 'Fighting'},
    'fire': {'ids': [18, 19], 'gender': {19: 'Male', 18: 'Female'}, 'display': 'Fire', 'button_label': 'Fire'},
    'flying': {'ids': [20, 21], 'gender': {21: 'Male', 20: 'Female'}, 'display': 'Flying', 'button_label': 'Flying'},
    'grass': {'ids': [22, 23], 'gender': {23: 'Male', 22: 'Female'}, 'display': 'Grass', 'button_label': 'Grass'},
    'ground': {'ids': [24, 25], 'gender': {25: 'Male', 24: 'Female'}, 'display': 'Ground', 'button_label': 'Ground'},
    'ice': {'ids': [26, 27], 'gender': {27: 'Male', 26: 'Female'}, 'display': 'Ice', 'button_label': 'Ice'},
    'metal': {'ids': [28, 29], 'gender': {29: 'Male', 28: 'Female'}, 'display': 'Metal', 'button_label': 'Metal'},
    'normal': {'ids': [30, 31], 'gender': {31: 'Male', 30: 'Female'}, 'display': 'Normal', 'button_label': 'Normal'},
    'poison': {'ids': [32, 33], 'gender': {33: 'Male', 32: 'Female'}, 'display': 'Poison', 'button_label': 'Poison'},
    'psychic': {'ids': [34, 35], 'gender': {35: 'Male', 34: 'Female'}, 'display': 'Psychic', 'button_label': 'Psychic'},
    'rock': {'ids': [36, 37], 'gender': {37: 'Male', 36: 'Female'}, 'display': 'Rock', 'button_label': 'Rock'},
    'waterfemale': {'ids': [38], 'gender': {38: 'Female'}, 'display': 'Water', 'button_label': 'Water (Female)'},
    'watermale': {'ids': [39], 'gender': {39: 'Male'}, 'display': 'Water', 'button_label': 'Water (Male)'},
    'electric': {'ids': [48, 49], 'gender': {49: 'Male', 48: 'Female'}, 'display': 'Electric', 'button_label': 'Electric'},
    'ghost': {'ids': [47, 48], 'gender': {47: 'Male', 48: 'Female'}, 'display': 'Ghost', 'button_label': 'Ghost'}
}

# API endpoints - REORDERED: NYC > Sydney > London > Singapore > Vancouver
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Sydney': 'https://sydneypogomap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php'
}

class TypeManager:
    """Thread-safe manager for pokestop types with deadlock prevention."""
    
    def __init__(self):
        self._lock = RLock()
        self._active_types = set(['fairy'])
        self._updater_threads = {}
        self._stop_events = {}
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._shutdown = False
        
        os.makedirs(CACHE_DIR, exist_ok=True)
    
    def is_type_active(self, pokestop_type: str) -> bool:
        with self._lock:
            return pokestop_type in self._active_types
    
    def start_type_updater(self, pokestop_type: str, type_info: dict) -> bool:
        with self._lock:
            if self._shutdown or pokestop_type in self._active_types:
                return True
            
            try:
                self._initialize_cache(pokestop_type)
                stop_event = Event()
                self._stop_events[pokestop_type] = stop_event
                
                self._executor.submit(self._immediate_fetch, pokestop_type, type_info)
                
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
                self._stop_events.pop(pokestop_type, None)
                return False
    
    def shutdown(self):
        with self._lock:
            self._shutdown = True
            for pokestop_type in list(self._active_types):
                if pokestop_type in self._stop_events:
                    self._stop_events[pokestop_type].set()
            self._executor.shutdown(wait=True)
    
    def _initialize_cache(self, pokestop_type: str):
        cache_file = self._get_cache_file(pokestop_type)
        if not os.path.exists(cache_file):
            empty_cache = {
                'stops': {location: [] for location in API_ENDPOINTS.keys()},
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self._write_cache(pokestop_type, empty_cache)
    
    def _immediate_fetch(self, pokestop_type: str, type_info: dict):
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
    
    def _update_cache_loop(self, pokestop_type: str, type_info: dict, stop_event: Event):
        data_fetcher = DataFetcher()
        
        while not stop_event.is_set() and not self._shutdown:
            try:
                if stop_event.wait(timeout=UPDATE_INTERVAL):
                    break
                
                stops_by_location = data_fetcher.fetch_all_locations(pokestop_type, type_info)
                
                cache_data = {
                    'stops': stops_by_location,
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                
                self._write_cache(pokestop_type, cache_data)
                logger.info(f"Cache updated for {pokestop_type}")
                
            except Exception as e:
                logger.error(f"Error in update loop for {pokestop_type}: {e}")
    
    def _get_cache_file(self, pokestop_type: str) -> str:
        return os.path.join(CACHE_DIR, f'pokestops_{pokestop_type}.json.gz')
    
    def _write_cache(self, pokestop_type: str, data: dict) -> bool:
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
    
    def read_cache(self, pokestop_type: str) -> dict:
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

class DataFetcher:
    """Handles data fetching from API endpoints."""
    
    def __init__(self):
        proxy_host = os.environ.get('NORDVPN_PROXY_HOST')
        proxy_user = os.environ.get('NORDVPN_PROXY_USER')
        proxy_pass = os.environ.get('NORDVPN_PROXY_PASS')
        
        if proxy_host and proxy_user and proxy_pass:
            proxy_url = f'socks5://{proxy_user}:{proxy_pass}@{proxy_host}:1080'
            self.proxies = {'http': proxy_url, 'https': proxy_url}
        else:
            self.proxies = None
    
    def fetch_all_locations(self, pokestop_type: str, type_info: dict) -> dict:
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
    
    def fetch_location_data(self, location: str, url: str, pokestop_type: str, type_info: dict) -> list:
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
    
    def _process_stops(self, data: dict, location: str, pokestop_type: str, type_info: dict) -> list:
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
    
    def _matches_type(self, character_id: int, grunt_dialogue: str, pokestop_type: str, character_ids: list) -> bool:
        # Direct character ID match
        if character_id in character_ids:
            # Special handling for electric type (shares IDs with ghost)
            if pokestop_type == 'electric':
                return any(kw in grunt_dialogue for kw in ['shock', 'electric', 'volt', 'charge'])
            return True
        
        # Grunt type matching
        if pokestop_type.startswith('grunt') and 'grunt' in grunt_dialogue:
            return True
        
        # Type dialogue matching (not for grunt types)
        if not pokestop_type.startswith('grunt'):
            # Water types - check for water dialogue
            if pokestop_type in ['waterfemale', 'watermale']:
                return 'water' in grunt_dialogue
            
            # Regular type matching
            if pokestop_type.lower() in grunt_dialogue:
                return True
            
            # Ghost special case
            if pokestop_type == 'ghost' and 'ke...ke...' in grunt_dialogue:
                return True
        
        return False

# Global type manager instance
type_manager = TypeManager()
type_manager.start_type_updater('fairy', POKESTOP_TYPES['fairy'])

# Graceful shutdown handling
def signal_handler(signum, frame):
    logger.info("Shutting down gracefully...")
    type_manager.shutdown()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Modern HTML template with proper button labels
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ display_title }}-Type PokéStops</title>
    <meta http-equiv="refresh" content="60">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #333;
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            backdrop-filter: blur(10px);
        }

        h1 {
            text-align: center;
            color: #2c3e50;
            margin-bottom: 10px;
            font-size: 2.5em;
            font-weight: 700;
        }

        .subtitle {
            text-align: center;
            color: #7f8c8d;
            margin-bottom: 30px;
            font-size: 1.1em;
        }

        .controls {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
            justify-content: center;
        }

        .type-selector {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
        }

        .type-link {
            padding: 8px 16px;
            background: #fff;
            color: #495057;
            text-decoration: none;
            border-radius: 20px;
            border: 2px solid #e9ecef;
            transition: all 0.3s ease;
            font-weight: 500;
        }

        .type-link:hover, .type-link.active {
            background: #007bff;
            color: white;
            border-color: #007bff;
            transform: translateY(-2px);
        }

        .location-section {
            background: white;
            border-radius: 12px;
            margin-bottom: 25px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.07);
        }

        .location-header {
            background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%);
            color: white;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .location-title {
            font-size: 1.4em;
            font-weight: 600;
        }

        .sort-btn {
            background: rgba(255,255,255,0.2);
            color: white;
            border: 1px solid rgba(255,255,255,0.3);
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 0.9em;
        }

        .sort-btn:hover {
            background: rgba(255,255,255,0.3);
        }

        .stops-list {
            padding: 0;
            list-style: none;
        }

        .stop-item {
            padding: 15px 20px;
            border-bottom: 1px solid #f1f3f4;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: background 0.2s ease;
        }

        .stop-item:hover {
            background: #f8f9fa;
        }

        .stop-item:last-child {
            border-bottom: none;
        }

        .stop-main {
            flex: 1;
        }

        .stop-name {
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 4px;
        }

        .stop-details {
            color: #6c757d;
            font-size: 0.9em;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }

        .stop-link {
            color: #007bff;
            text-decoration: none;
        }

        .stop-link:hover {
            text-decoration: underline;
        }

        .stop-meta {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 4px;
        }

        .time-remaining {
            background: #28a745;
            color: white;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 500;
        }

        .distance-info {
            background: #6c757d;
            color: white;
            padding: 2px 6px;
            border-radius: 8px;
            font-size: 0.75em;
        }

        .no-stops {
            text-align: center;
            color: #6c757d;
            padding: 40px 20px;
            font-style: italic;
        }

        .loading {
            color: #ff9900;
        }

        .debug {
            font-size: 0.8em;
            color: #6c757d;
            margin-top: 4px;
        }

        @media (max-width: 768px) {
            body {
                padding: 10px;
            }
            
            .container {
                padding: 20px;
            }
            
            h1 {
                font-size: 2em;
            }
            
            .controls {
                padding: 15px;
            }
            
            .location-header {
                flex-direction: column;
                gap: 10px;
                text-align: center;
            }
            
            .stop-item {
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
            }
            
            .stop-meta {
                align-items: flex-start;
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
        
        // Distance calculation function
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
        
        // Cooldown calculation based on distance
        function getCooldownTime(distanceKm) {
            if (distanceKm <= 1) return "< 1 min";
            if (distanceKm <= 2) return "1 min";
            if (distanceKm <= 3) return "< 2 min";
            if (distanceKm <= 5) return "2 min";
            if (distanceKm <= 7) return "5 min";
            if (distanceKm <= 9) return "< 7 min";
            if (distanceKm <= 10) return "7 min";
            if (distanceKm <= 12) return "8 min";
            if (distanceKm <= 18) return "10 min";
            if (distanceKm <= 26) return "15 min";
            if (distanceKm <= 42) return "19 min";
            if (distanceKm <= 65) return "22 min";
            if (distanceKm <= 76) return "< 25 min";
            if (distanceKm <= 81) return "25 min";
            if (distanceKm <= 100) return "35 min";
            if (distanceKm <= 220) return "< 40 min";
            if (distanceKm <= 250) return "45 min";
            if (distanceKm <= 350) return "< 51 min";
            if (distanceKm <= 375) return "54 min";
            if (distanceKm <= 460) return "62 min";
            if (distanceKm <= 500) return "< 65 min";
            if (distanceKm <= 565) return "69 min";
            if (distanceKm <= 700) return "78 min";
            if (distanceKm <= 800) return "84 min";
            if (distanceKm <= 900) return "92 min";
            if (distanceKm <= 1000) return "99 min";
            if (distanceKm <= 1100) return "107 min";
            if (distanceKm <= 1200) return "< 114 min";
            if (distanceKm <= 1300) return "117 min";
            if (distanceKm <= 1350) return "2 hours";
            return "2+ hours";
        }
        
        // Nearest neighbor algorithm
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
        
        // Render stops with distance information
        function renderStops(location, stops) {
            let ul = document.getElementById('stops-list-' + location);
            ul.innerHTML = '';
            
            stops.forEach((stop, index) => {
                let li = document.createElement('li');
                li.className = 'stop-item';
                
                let distanceHtml = '';
                if (sortMode[location] === 'nearest' && index < stops.length - 1) {
                    let nextStop = stops[index + 1];
                    let dist = distance(stop, nextStop);
                    let cooldown = getCooldownTime(dist);
                    distanceHtml = `<span class="distance-info">${dist.toFixed(1)}km • ${cooldown}</span>`;
                }
                
                let timeMinutes = Math.floor(stop.remaining_time / 60);
                let timeSeconds = Math.floor(stop.remaining_time % 60);
                
                let debugHtml = '';
                if (isDebug) {
                    debugHtml = `<div class="debug">Character: ${stop.character}, Dialogue: ${stop.grunt_dialogue || 'N/A'}, Encounter ID: ${stop.encounter_pokemon_id || 'N/A'}</div>`;
                }
                
                li.innerHTML = `
                    <div class="stop-main">
                        <div class="stop-name">${stop.type} (${stop.gender}) ${stop.name}</div>
                        <div class="stop-details">
                            <span><a href="https://maps.google.com/?q=${stop.lat},${stop.lng}" class="stop-link" target="_blank">${stop.lat}, ${stop.lng}</a></span>
                        </div>
                        ${debugHtml}
                    </div>
                    <div class="stop-meta">
                        <span class="time-remaining">${timeMinutes}m ${timeSeconds}s</span>
                        ${distanceHtml}
                    </div>
                `;
                ul.appendChild(li);
            });
        }
        
        // Toggle sorting method
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
        
        // Initialize on page load
        document.addEventListener('DOMContentLoaded', function() {
            // Initialize all locations with time-based sorting
            Object.keys(stopsData).forEach(location => {
                sortMode[location] = 'time';
                renderStops(location, stopsData[location]);
            });
        });
    </script>
</head>
<body>
    <div class="container">
        <h1>{{ display_title }}-Type PokéStops</h1>
        <div class="subtitle">Last updated: {{ last_updated }} • Updates every minute</div>
        
        <div class="controls">
            <div class="type-selector">
                {% for type_key, type_info in types.items() %}
                    <a href="?type={{ type_key }}{% if debug %}&debug=true{% endif %}" 
                       class="type-link{% if type_key == pokestop_type %} active{% endif %}">
                        {{ type_info.button_label }}
                    </a>
                {% endfor %}
            </div>
        </div>
        
        {% for location, location_stops in stops.items() %}
            <div class="location-section">
                <div class="location-header">
                    <div class="location-title">{{ location }}</div>
                    <button id="sort-btn-{{ location }}" class="sort-btn" onclick="toggleSort('{{ location }}')">
                        Sort by Nearest Neighbor
                    </button>
                </div>
                
                {% if location_stops %}
                    <ul id="stops-list-{{ location }}" class="stops-list">
                        <!-- Stops will be rendered by JavaScript -->
                    </ul>
                {% else %}
                    <div class="no-stops">
                        {% if last_updated == 'Unknown' %}
                            <span class="loading">Loading {{ display_title }}-type data...</span>
                        {% else %}
                            No {{ display_title }}-type PokéStops found in {{ location }}.
                        {% endif %}
                    </div>
                {% endif %}
            </div>
        {% endfor %}
    </div>
</body>
</html>
"""

@app.route('/')
def get_pokestops():
    """Main route for displaying pokestops."""
    from collections import OrderedDict  # Add this import
    
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
    
    # Get stops data
    stops_data = data.get('stops', {location: [] for location in API_ENDPOINTS.keys()})
    
    # Create OrderedDict with correct location order
    stops = OrderedDict()
    for location in ['NYC', 'Sydney', 'London', 'Singapore', 'Vancouver']:
        if location in stops_data:
            stops[location] = sorted(stops_data[location], key=lambda s: s['remaining_time'], reverse=True)
        else:
            stops[location] = []
    
    # Get display title from type_info
    display_title = type_info.get('button_label', type_info.get('display', pokestop_type.capitalize()))
    
    try:
        return render_template_string(
            HTML_TEMPLATE,
            stops=stops,  # Now using ordered stops
            last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            pokestop_type=pokestop_type,
            display_title=display_title,
            types=POKESTOP_TYPES,
            debug=debug
        )
    except Exception as e:
        logger.error(f"Render failed for {pokestop_type}: {e}")
        # Also create ordered dict for error case
        stops = OrderedDict()
        for location in ['NYC', 'Sydney', 'London', 'Singapore', 'Vancouver']:
            stops[location] = []
        
        return render_template_string(
            HTML_TEMPLATE,
            stops=stops,
            last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            pokestop_type=pokestop_type,
            display_title=display_title,
            types=POKESTOP_TYPES,
            debug=debug
        ), 500

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