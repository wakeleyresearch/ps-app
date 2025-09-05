from flask import Flask, render_template_string, request, jsonify
import requests
from datetime import datetime, timedelta
import time
import threading
import json
import os
import psutil
from threading import Lock, Event
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from collections import deque, defaultdict
import gc
import traceback

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cache update intervals - Optimized for 2GB memory
INITIAL_UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', 120))  # 2 minutes for active types
IDLE_UPDATE_INTERVAL = int(os.getenv('IDLE_UPDATE_INTERVAL', 600))  # 10 minutes for idle types
LAST_ACCESS_TIMEOUT = 300  # 5 minutes - mark type as idle if not accessed

# Time filters
MIN_REMAINING_TIME = 180
MAX_REMAINING_TIME = 7200

# Concurrent limits - Optimized for 2GB/1CPU Render Starter
MAX_CONCURRENT_TYPES = int(os.getenv('MAX_CONCURRENT_TYPES', 10))  # 10 types safe for 2GB
MAX_CONCURRENT_LOCATIONS = int(os.getenv('MAX_CONCURRENT_LOCATIONS', 5))
MAX_QUEUED_TYPES = 15

# Type management
active_types = {}
active_types_lock = Lock()
type_queue = deque(maxlen=MAX_QUEUED_TYPES)
type_queue_lock = Lock()
stop_events = {}

# Store recent logs for debug
recent_logs = deque(maxlen=200)
class DebugLogHandler(logging.Handler):
    def emit(self, record):
        recent_logs.append({
            'time': datetime.now().isoformat(),
            'level': record.levelname,
            'message': self.format(record)
        })

debug_handler = DebugLogHandler()
debug_handler.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(debug_handler)

# Grunt type configuration with proper display names
POKESTOP_TYPES = {
    # Grunt types separated by gender
    'gruntmale': {'ids': [4], 'gender': {4: 'Male'}, 'display': 'Grunt (Male)', 'dialogue_keywords': ['grunt']},
    'gruntfemale': {'ids': [5], 'gender': {5: 'Female'}, 'display': 'Grunt (Female)', 'dialogue_keywords': ['grunt']},
    
    # Water types separated by gender  
    'watermale': {'ids': [39], 'gender': {39: 'Male'}, 'display': 'Water (Male)', 'dialogue_keywords': ['water', 'splash', 'ocean', 'sea']},
    'waterfemale': {'ids': [38], 'gender': {38: 'Female'}, 'display': 'Water (Female)', 'dialogue_keywords': ['water', 'splash', 'ocean', 'sea']},
    
    # All other types with dialogue keywords
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
    'ghost': {'ids': [47, 48], 'gender': {47: 'Male', 48: 'Female'}, 'display': 'Ghost', 'dialogue_keywords': ['ghost', 'ke...ke...', 'boo', 'spirit']},
    'electric': {'ids': [48, 49], 'gender': {49: 'Male', 48: 'Female'}, 'display': 'Electric', 'dialogue_keywords': ['electric', 'shock', 'volt', 'charge', 'zap', 'thunder', 'spark']}
}

# Display names mapping for UI (fixes waterfemale label bug)
POKESTOP_DISPLAY_NAMES = {
    'gruntmale': 'Grunt (Male)',
    'gruntfemale': 'Grunt (Female)',
    'watermale': 'Water (Male)',
    'waterfemale': 'Water (Female)',
    'bug': 'Bug',
    'dark': 'Dark',
    'dragon': 'Dragon',
    'fairy': 'Fairy',
    'fighting': 'Fighting',
    'fire': 'Fire',
    'flying': 'Flying',
    'grass': 'Grass',
    'ground': 'Ground',
    'ice': 'Ice',
    'metal': 'Metal',
    'normal': 'Normal',
    'poison': 'Poison',
    'psychic': 'Psychic',
    'rock': 'Rock',
    'ghost': 'Ghost',
    'electric': 'Electric'
}

# API endpoints
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php',
    'Sydney': 'https://sydneypogomap.com/pokestop.php'
}

# Cooldown time mapping
COOLDOWN_MAP = [
    (1, 1), (2, 1), (3, 2), (5, 2), (7, 5), (9, 7), (10, 7), (12, 8),
    (18, 10), (26, 15), (42, 19), (65, 22), (76, 25), (81, 25), (90, 35),
    (220, 40), (250, 45), (350, 51), (375, 54), (460, 62), (500, 65),
    (565, 69), (700, 78), (800, 84), (900, 92), (1000, 99), (1100, 107),
    (1200, 114), (1300, 117), (1350, 120)
]

def get_cooldown_time(distance_km):
    """Get cooldown time in minutes for a given distance in km"""
    for dist, cooldown in COOLDOWN_MAP:
        if distance_km <= dist:
            return cooldown
    return 120

def get_cache_file(pokestop_type):
    """Return cache file path for the given type."""
    cache_dir = '/tmp'  # Use /tmp for Render's ephemeral storage
    return os.path.join(cache_dir, f'pokestops_{pokestop_type}.json')

def initialize_cache(pokestop_type):
    """Initialize cache file for the given type with proper error handling."""
    cache_file = get_cache_file(pokestop_type)
    try:
        cache_dir = os.path.dirname(cache_file)
        os.makedirs(cache_dir, exist_ok=True)
        
        empty_cache = {
            'stops': {location: [] for location in API_ENDPOINTS.keys()},
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        with open(cache_file, 'w') as f:
            json.dump(empty_cache, f)
        
        logger.info(f"✅ Initialized cache file for {pokestop_type}")
        return True
    except Exception as e:
        logger.error(f"⚠️ Failed to initialize cache file for {pokestop_type}: {e}")
        return False

def get_container_memory_stats():
    """Get actual container memory stats from cgroup (works in Docker/containers)"""
    try:
        # Try to read from cgroup v2 first (newer systems)
        with open('/sys/fs/cgroup/memory.current', 'r') as f:
            used = int(f.read().strip())
        with open('/sys/fs/cgroup/memory.max', 'r') as f:
            limit = int(f.read().strip())
        
        if limit > 9223372036854775807:  # Max value means no limit
            limit = 2 * 1024 * 1024 * 1024  # Default to 2GB if no limit
            
        return {
            'used_mb': used / 1024 / 1024,
            'limit_mb': limit / 1024 / 1024,
            'percent': (used / limit) * 100 if limit > 0 else 0
        }
    except FileNotFoundError:
        try:
            # Try cgroup v1 (older systems)
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
                used = int(f.read().strip())
            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
                limit = int(f.read().strip())
            
            if limit > 9223372036854775807:  # Max value means no limit
                limit = 2 * 1024 * 1024 * 1024  # Default to 2GB
                
            return {
                'used_mb': used / 1024 / 1024,
                'limit_mb': limit / 1024 / 1024,
                'percent': (used / limit) * 100 if limit > 0 else 0
            }
        except:
            # Fallback to psutil but with manual limit
            memory = psutil.virtual_memory()
            return {
                'used_mb': (memory.total - memory.available) / 1024 / 1024,
                'limit_mb': 2048,  # Your known limit
                'percent': min(((memory.total - memory.available) / (2 * 1024 * 1024 * 1024)) * 100, 100)
            }

def fetch_location(location, url, character_ids, gender_map, display_type, pokestop_type, type_info):
    """Fetch data for a single location with better error handling."""
    try:
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
        dialogue_keywords = type_info.get('dialogue_keywords', [])
        
        for stop in data.get('invasions', []):
            try:
                character_id = stop.get('character')
                if character_id not in character_ids:
                    continue
                    
                remaining_time = stop.get('invasion_end', 0) - (current_time - time_offset)
                
                if not (MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME):
                    continue
                
                grunt_dialogue = stop.get('grunt_dialogue', '').lower()
                dialogue_match = True  # Default to true for character ID matches
                
                stops.append({
                    'lat': stop.get('lat', 0),
                    'lng': stop.get('lng', 0),
                    'name': stop.get('name', f'Unnamed PokéStop ({location})'),
                    'remaining_time': remaining_time,
                    'character': character_id,
                    'type': display_type,
                    'gender': gender_map.get(character_id, 'Unknown'),
                    'grunt_dialogue': grunt_dialogue,
                    'encounter_pokemon_id': stop.get('encounter_pokemon_id', None),
                    'dialogue_match': dialogue_match
                })
            except Exception as e:
                logger.debug(f"Error processing stop: {e}")
                continue
        
        logger.info(f"✅ Fetched {len(stops)} {display_type} PokéStops for {location}")
        return location, stops
        
    except Exception as e:
        logger.error(f"❌ Error fetching data for {location} ({pokestop_type}): {e}")
        return location, []

def update_cache_smart(pokestop_type, type_info):
    """Smart cache updater with better error handling."""
    cache_file = get_cache_file(pokestop_type)
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    
    stop_event = Event()
    with active_types_lock:
        stop_events[pokestop_type] = stop_event
    
    try:
        while not stop_event.is_set():
            with active_types_lock:
                if pokestop_type not in active_types:
                    logger.info(f"🛑 Stopping updater for {pokestop_type} - no longer active")
                    break
                
                last_access = active_types[pokestop_type]['last_access']
                time_since_access = time.time() - last_access
                
                if time_since_access > LAST_ACCESS_TIMEOUT:
                    update_interval = IDLE_UPDATE_INTERVAL
                else:
                    update_interval = INITIAL_UPDATE_INTERVAL
                
                active_types[pokestop_type]['update_interval'] = update_interval
            
            try:
                stops_by_location = {}
                
                with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LOCATIONS) as executor:
                    futures = []
                    for location, url in API_ENDPOINTS.items():
                        future = executor.submit(
                            fetch_location, 
                            location, url, character_ids, gender_map, 
                            display_type, pokestop_type, type_info
                        )
                        futures.append(future)
                    
                    for future in as_completed(futures):
                        location, stops = future.result()
                        stops_by_location[location] = stops

                # Write cache
                cache_dir = os.path.dirname(cache_file)
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_file, 'w') as f:
                    json.dump({
                        'stops': stops_by_location,
                        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }, f)
                logger.info(f"✅ Cache updated for {pokestop_type}")
                
            except Exception as e:
                logger.error(f"❌ Error updating cache for {pokestop_type}: {e}")
            
            stop_event.wait(timeout=update_interval)
            
    finally:
        with active_types_lock:
            if pokestop_type in active_types:
                del active_types[pokestop_type]
            if pokestop_type in stop_events:
                del stop_events[pokestop_type]
        logger.info(f"🔚 Updater thread ended for {pokestop_type}")

def process_queue():
    """Background thread to process queued types."""
    while True:
        try:
            with type_queue_lock:
                if type_queue:
                    with active_types_lock:
                        if len(active_types) < MAX_CONCURRENT_TYPES:
                            pokestop_type = type_queue.popleft()
                            
                            if pokestop_type not in active_types:
                                logger.info(f"📤 Processing queued type: {pokestop_type}")
                                start_type_updater(pokestop_type)
                            
            time.sleep(5)
        except Exception as e:
            logger.error(f"Error in queue processor: {e}")
            time.sleep(5)

def start_type_updater(pokestop_type):
    """Start updater for a type with better error handling."""
    if pokestop_type not in POKESTOP_TYPES:
        logger.error(f"Invalid type: {pokestop_type}")
        return False
    
    try:
        type_info = POKESTOP_TYPES[pokestop_type]
        
        if not initialize_cache(pokestop_type):
            logger.error(f"Failed to initialize cache for {pokestop_type}")
        
        thread = threading.Thread(
            target=update_cache_smart, 
            args=(pokestop_type, type_info),
            daemon=True,
            name=f"Updater-{pokestop_type}"
        )
        thread.start()
        
        with active_types_lock:
            active_types[pokestop_type] = {
                'thread': thread,
                'last_access': time.time(),
                'update_interval': INITIAL_UPDATE_INTERVAL
            }
        
        logger.info(f"🚀 Started updater for {pokestop_type}")
        return True
    except Exception as e:
        logger.error(f"Failed to start updater for {pokestop_type}: {e}")
        return False

def fetch_initial_data(pokestop_type, type_info):
    """Fetch initial data with better error handling."""
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    cache_file = get_cache_file(pokestop_type)
    
    try:
        location, stops = fetch_location(
            'NYC', API_ENDPOINTS['NYC'], 
            character_ids, gender_map, display_type, 
            pokestop_type, type_info
        )
        
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
        
        cache_dir = os.path.dirname(cache_file)
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file, 'w') as f:
            json.dump(initial_data, f)
        
        logger.info(f"✅ Initial data fetched for {pokestop_type}: {len(stops)} stops from NYC")
        
    except Exception as e:
        logger.error(f"⚠️ Failed to fetch initial data for {pokestop_type}: {e}")
        initialize_cache(pokestop_type)

# Initialize queue processor
threading.Thread(target=process_queue, daemon=True, name="QueueProcessor").start()

# Start default types (most popular for 2GB memory)
DEFAULT_TYPES = ['fairy', 'gruntmale', 'gruntfemale', 'dragon', 'watermale']
for pokestop_type in DEFAULT_TYPES:
    start_type_updater(pokestop_type)

# Health check endpoint
@app.route('/health')
def health():
    """Health check endpoint for Render"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

# Debug status endpoint with accurate container memory
@app.route('/debug/status')
def debug_status():
    """System status with accurate container memory"""
    with active_types_lock:
        active_list = list(active_types.keys())
        active_details = {
            k: {
                'last_access': int(time.time() - v['last_access']),
                'update_interval': v['update_interval']
            } for k, v in active_types.items()
        }
    
    with type_queue_lock:
        queued = list(type_queue)
    
    # Get real container memory stats
    mem_stats = get_container_memory_stats()
    cpu_percent = psutil.cpu_percent(interval=1)
    
    # Calculate estimated memory per type
    estimated_mb_per_type = 35
    max_safe_types = int((mem_stats['limit_mb'] * 0.7) / estimated_mb_per_type)
    
    return jsonify({
        'status': 'running',
        'active_types': active_list,
        'active_details': active_details,
        'queued_types': queued,
        'active_count': len(active_list),
        'max_concurrent': MAX_CONCURRENT_TYPES,
        'recommended_max_types': max_safe_types,
        'queue_length': len(queued),
        'container_memory': {
            'used_mb': round(mem_stats['used_mb'], 1),
            'limit_mb': round(mem_stats['limit_mb'], 1),
            'percent': round(mem_stats['percent'], 1),
            'estimated_mb_per_type': estimated_mb_per_type
        },
        'cpu': {
            'percent': cpu_percent,
            'cores': 1
        },
        'intervals': {
            'initial_update': INITIAL_UPDATE_INTERVAL,
            'idle_update': IDLE_UPDATE_INTERVAL,
            'last_access_timeout': LAST_ACCESS_TIMEOUT
        }
    })

# Main route with better error handling
@app.route('/')
def get_pokestops():
    try:
        pokestop_type = request.args.get('type', 'fairy').lower()
        debug = request.args.get('debug', 'false').lower() == 'true'
        
        if pokestop_type not in POKESTOP_TYPES:
            pokestop_type = 'fairy'
        
        cache_file = get_cache_file(pokestop_type)
        type_info = POKESTOP_TYPES[pokestop_type]
        
        # Update last access time
        with active_types_lock:
            if pokestop_type in active_types:
                active_types[pokestop_type]['last_access'] = time.time()
        
        # Check if type needs to be started
        cache_exists = os.path.exists(cache_file)
        
        if not cache_exists:
            initialize_cache(pokestop_type)
            fetch_initial_data(pokestop_type, type_info)
        
        with active_types_lock:
            if pokestop_type not in active_types:
                if len(active_types) >= MAX_CONCURRENT_TYPES:
                    with type_queue_lock:
                        if pokestop_type not in type_queue:
                            type_queue.append(pokestop_type)
                            logger.info(f"📋 Added {pokestop_type} to queue")
                else:
                    start_type_updater(pokestop_type)
        
        # Load cache with fallback
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            logger.debug(f"📖 Loaded cache for {pokestop_type}")
        except Exception as e:
            logger.error(f"⚠️ Error reading cache for {pokestop_type}: {e}")
            data = {
                'stops': {location: [] for location in API_ENDPOINTS.keys()}, 
                'last_updated': 'Initializing...'
            }
        
        # Sort stops
        stops = data.get('stops', {})
        for location in API_ENDPOINTS.keys():
            if location not in stops:
                stops[location] = []
            else:
                stops[location] = sorted(stops[location], key=lambda s: s.get('remaining_time', 0), reverse=True)
        
        # Get status info
        with active_types_lock:
            active_types_list = ', '.join(sorted(active_types.keys())) if active_types else 'None'
        with type_queue_lock:
            queued_types_list = ', '.join(list(type_queue)) if type_queue else 'None'
        
        return render_template_string(
            HTML_TEMPLATE,
            stops=stops,
            last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            pokestop_type=pokestop_type,
            pokestop_type_display=type_info['display'],
            types=POKESTOP_TYPES.keys(),
            display_names=POKESTOP_DISPLAY_NAMES,
            active_types_list=active_types_list,
            queued_types_list=queued_types_list,
            debug=debug
        )
    except Exception as e:
        logger.error(f"Error in main route: {e}\n{traceback.format_exc()}")
        return f"""
        <html>
        <head><title>Error</title></head>
        <body>
            <h1>Error Loading PokéStops</h1>
            <p>An error occurred while loading the page. Please try again.</p>
            <p>Error: {str(e)}</p>
            <p><a href="/">Go to Home</a></p>
        </body>
        </html>
        """, 500

# HTML template with fixed button labels
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
        .queued-types {
            background: #fff3cd;
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
    </style>
</head>
<body>
    <div class="container">
        <h1>{{ pokestop_type_display }} PokéStops</h1>
        <p class="info-text">Last updated: {{ last_updated }}</p>
        <p class="info-text">Updates every 2-10 minutes based on activity.</p>
        <div class="active-types">
            <strong>Currently updating:</strong> {{ active_types_list }}
        </div>
        {% if queued_types_list != 'None' %}
        <div class="queued-types">
            <strong>Queued for update:</strong> {{ queued_types_list }}
        </div>
        {% endif %}
        
        <div class="type-selector">
            <strong>Select Type:</strong>
            <div class="type-buttons">
                {% for type_key in types %}
                    <a href="?type={{ type_key }}{% if debug %}&debug=true{% endif %}" 
                       class="type-btn {% if pokestop_type == type_key %}active{% endif %}">
                        {{ display_names.get(type_key, type_key.title()) }}
                    </a>
                {% endfor %}
            </div>
        </div>
        
        {% for location, location_stops in stops.items() %}
            <h2>{{ location }}</h2>
            {% if location_stops %}
                <ul>
                    {% for stop in location_stops %}
                        <li>{{ stop.type }} ({{ stop.gender }}) {{ stop.name }} (<a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}">{{ "%.6f"|format(stop.lat) }}, {{ "%.6f"|format(stop.lng) }}</a>) - {{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining
                            {% if debug %}
                                <div class="debug">Character: {{ stop.character }}, Dialogue: {{ stop.grunt_dialogue|default('N/A')|truncate(50) }}</div>
                            {% endif %}
                        </li>
                    {% endfor %}
                </ul>
            {% else %}
                <p class="no-stops">No {{ pokestop_type_display }} PokéStops found in {{ location }}. Data is loading...</p>
            {% endif %}
        {% endfor %}
    </div>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
