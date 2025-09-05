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

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cache update intervals
INITIAL_UPDATE_INTERVAL = 120  # 2 minutes for active types
IDLE_UPDATE_INTERVAL = 600     # 10 minutes for idle types
LAST_ACCESS_TIMEOUT = 300       # 5 minutes - mark type as idle if not accessed

# Time filters
MIN_REMAINING_TIME = 180
MAX_REMAINING_TIME = 7200

# Concurrent limits - Increased for better server utilization
MAX_CONCURRENT_TYPES = 8  # Increased from 3
MAX_CONCURRENT_LOCATIONS = 5
MAX_QUEUED_TYPES = 10

# Type management
active_types = {}  # {type: {'thread': thread, 'last_access': time, 'update_interval': seconds}}
active_types_lock = Lock()
type_queue = deque(maxlen=MAX_QUEUED_TYPES)
type_queue_lock = Lock()
stop_events = {}  # Events to stop update threads

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

# Grunt type configuration
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
    'ghost': {'ids': [47, 48], 'gender': {47: 'Male', 48: 'Female'}, 'display': 'Ghost', 'dialogue_keywords': ['ghost', 'ke...ke...', 'boo', 'spirit']},
    'electric': {'ids': [48, 49], 'gender': {49: 'Male', 48: 'Female'}, 'display': 'Electric', 'dialogue_keywords': ['electric', 'shock', 'volt', 'charge', 'zap', 'thunder', 'spark']}
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
            logger.info(f"✅ Initialized cache file for {pokestop_type}")
    except Exception as e:
        logger.error(f"⚠️ Failed to initialize cache file for {pokestop_type}: {e}")

def fetch_location(location, url, character_ids, gender_map, display_type, pokestop_type, type_info):
    """Fetch data for a single location."""
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
        invasion_count = len(data.get('invasions', []))
        dialogue_keywords = type_info.get('dialogue_keywords', [])
        
        logger.info(f"📊 {location}: Found {invasion_count} total invasions for {pokestop_type}")
        
        for stop in data.get('invasions', []):
            character_id = stop.get('character')
            grunt_dialogue = stop.get('grunt_dialogue', '').lower()
            
            if character_id not in character_ids:
                continue
                
            remaining_time = stop['invasion_end'] - (current_time - time_offset)
            
            if not (MIN_REMAINING_TIME < remaining_time < MAX_REMAINING_TIME):
                continue
            
            dialogue_match = False
            
            if pokestop_type == 'ghost' and 'ke' in grunt_dialogue:
                dialogue_match = True
                logger.debug(f"👻 Ghost match - ID: {character_id}, Dialogue: {grunt_dialogue[:50]}")
            elif pokestop_type == 'electric' and character_id == 48:
                if any(kw in grunt_dialogue for kw in dialogue_keywords):
                    dialogue_match = True
                else:
                    logger.debug(f"⚡ Skipping ID 48 for electric - no keyword match")
                    continue
            elif dialogue_keywords and any(kw in grunt_dialogue for kw in dialogue_keywords):
                dialogue_match = True
            
            if not dialogue_match and pokestop_type not in ['gruntmale', 'gruntfemale']:
                logger.debug(f"⚠️ {pokestop_type} - ID {character_id} matched but no dialogue keywords")
            
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
                'dialogue_match': dialogue_match
            })
        
        logger.info(f"✅ Fetched {len(stops)} {display_type} PokéStops for {location}")
        return location, stops
        
    except Exception as e:
        logger.error(f"❌ Error fetching data for {location} ({pokestop_type}): {e}")
        return location, []

def update_cache_smart(pokestop_type, type_info):
    """Smart cache updater that adjusts frequency based on activity."""
    cache_file = get_cache_file(pokestop_type)
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    
    # Create stop event for this type
    stop_event = Event()
    with active_types_lock:
        stop_events[pokestop_type] = stop_event
    
    try:
        while not stop_event.is_set():
            # Check if this type should be running
            with active_types_lock:
                if pokestop_type not in active_types:
                    logger.info(f"🛑 Stopping updater for {pokestop_type} - no longer active")
                    break
                
                # Determine update interval based on last access
                last_access = active_types[pokestop_type]['last_access']
                time_since_access = time.time() - last_access
                
                if time_since_access > LAST_ACCESS_TIMEOUT:
                    update_interval = IDLE_UPDATE_INTERVAL
                    logger.debug(f"⏸️ {pokestop_type} is idle (not accessed for {int(time_since_access)}s)")
                else:
                    update_interval = INITIAL_UPDATE_INTERVAL
                
                active_types[pokestop_type]['update_interval'] = update_interval
            
            # Fetch data from all locations
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
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                with open(cache_file, 'w') as f:
                    json.dump({
                        'stops': stops_by_location,
                        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }, f)
                logger.info(f"✅ Cache updated for {pokestop_type}")
                
            except Exception as e:
                logger.error(f"❌ Error updating cache for {pokestop_type}: {e}")
            
            # Wait for next update or stop signal
            stop_event.wait(timeout=update_interval)
            
    finally:
        # Cleanup
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
                        # Check if we have capacity
                        if len(active_types) < MAX_CONCURRENT_TYPES:
                            pokestop_type = type_queue.popleft()
                            
                            if pokestop_type not in active_types:
                                # Start the type
                                logger.info(f"📤 Processing queued type: {pokestop_type}")
                                start_type_updater(pokestop_type)
                            
            time.sleep(5)  # Check queue every 5 seconds
        except Exception as e:
            logger.error(f"Error in queue processor: {e}")
            time.sleep(5)

def start_type_updater(pokestop_type):
    """Start updater for a type."""
    if pokestop_type not in POKESTOP_TYPES:
        return False
    
    type_info = POKESTOP_TYPES[pokestop_type]
    initialize_cache(pokestop_type)
    
    # Start update thread
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

def fetch_initial_data(pokestop_type, type_info):
    """Fetch initial data for a type."""
    character_ids = type_info['ids']
    gender_map = type_info['gender']
    display_type = type_info['display']
    cache_file = get_cache_file(pokestop_type)
    dialogue_keywords = type_info.get('dialogue_keywords', [])
    
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
        initial_data = {
            'stops': {location: [] for location in API_ENDPOINTS.keys()},
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        try:
            with open(cache_file, 'w') as f:
                json.dump(initial_data, f)
        except:
            pass

# Initialize queue processor
threading.Thread(target=process_queue, daemon=True, name="QueueProcessor").start()

# Start default types
DEFAULT_TYPES = ['fairy', 'gruntmale', 'gruntfemale']
for pokestop_type in DEFAULT_TYPES:
    start_type_updater(pokestop_type)

# Debug routes
@app.route('/debug/status')
def debug_status():
    """System status overview"""
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
    
    memory = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(interval=1)
    
    total_stops = 0
    cache_ages = {}
    for pokestop_type in POKESTOP_TYPES.keys():
        cache_file = get_cache_file(pokestop_type)
        if os.path.exists(cache_file):
            age = time.time() - os.path.getmtime(cache_file)
            cache_ages[pokestop_type] = f"{int(age/60)}min {int(age%60)}sec"
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    stops = data.get('stops', {})
                    type_total = sum(len(s) for s in stops.values())
                    total_stops += type_total
            except:
                pass
    
    return jsonify({
        'status': 'running',
        'active_types': active_list,
        'active_details': active_details,
        'queued_types': queued,
        'active_count': len(active_list),
        'max_concurrent': MAX_CONCURRENT_TYPES,
        'queue_length': len(queued),
        'total_cached_stops': total_stops,
        'cache_ages': cache_ages,
        'system': {
            'cpu_percent': cpu_percent,
            'memory_percent': memory.percent,
            'memory_available_mb': memory.available / 1024 / 1024,
            'memory_used_mb': memory.used / 1024 / 1024
        },
        'intervals': {
            'initial_update': INITIAL_UPDATE_INTERVAL,
            'idle_update': IDLE_UPDATE_INTERVAL,
            'last_access_timeout': LAST_ACCESS_TIMEOUT
        }
    })

@app.route('/debug/cache/<pokestop_type>')
def debug_cache(pokestop_type):
    """Detailed cache info for a specific type"""
    if pokestop_type not in POKESTOP_TYPES:
        return jsonify({'error': f'Invalid type: {pokestop_type}'}), 400
    
    cache_file = get_cache_file(pokestop_type)
    if not os.path.exists(cache_file):
        return jsonify({'error': f'No cache for {pokestop_type}'}), 404
    
    try:
        file_stats = os.stat(cache_file)
        with open(cache_file, 'r') as f:
            data = json.load(f)
        
        stops = data.get('stops', {})
        stop_counts = {loc: len(s) for loc, s in stops.items()}
        total_stops = sum(stop_counts.values())
        
        with active_types_lock:
            is_active = pokestop_type in active_types
            if is_active:
                last_access = int(time.time() - active_types[pokestop_type]['last_access'])
                update_interval = active_types[pokestop_type]['update_interval']
            else:
                last_access = None
                update_interval = None
        
        return jsonify({
            'type': pokestop_type,
            'display_name': POKESTOP_TYPES[pokestop_type]['display'],
            'expected_ids': POKESTOP_TYPES[pokestop_type]['ids'],
            'file_size_kb': file_stats.st_size / 1024,
            'last_modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
            'age_seconds': int(time.time() - file_stats.st_mtime),
            'last_updated': data.get('last_updated'),
            'total_stops': total_stops,
            'stops_by_location': stop_counts,
            'is_active': is_active,
            'last_access_seconds_ago': last_access,
            'update_interval': update_interval
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug/refresh/<pokestop_type>')
def debug_refresh(pokestop_type):
    """Force refresh a specific type"""
    if pokestop_type not in POKESTOP_TYPES:
        return jsonify({'error': f'Invalid type: {pokestop_type}'}), 400
    
    with active_types_lock:
        if pokestop_type in active_types:
            # Update last access to force active interval
            active_types[pokestop_type]['last_access'] = time.time()
            return jsonify({
                'message': f'{pokestop_type} already active, refreshed access time',
                'active_types': list(active_types.keys())
            })
        
        if len(active_types) >= MAX_CONCURRENT_TYPES:
            # Add to queue
            with type_queue_lock:
                if pokestop_type not in type_queue:
                    type_queue.append(pokestop_type)
                    return jsonify({
                        'message': f'Added {pokestop_type} to queue',
                        'queue_position': len(type_queue),
                        'active_types': list(active_types.keys())
                    })
            return jsonify({
                'message': f'{pokestop_type} already in queue',
                'active_types': list(active_types.keys())
            })
        
        # Start immediately
        success = start_type_updater(pokestop_type)
        if success:
            return jsonify({
                'message': f'Started updater for {pokestop_type}',
                'active_types': list(active_types.keys())
            })
        else:
            return jsonify({'error': 'Failed to start updater'}), 500

@app.route('/debug/stop/<pokestop_type>')
def debug_stop(pokestop_type):
    """Stop updating a specific type"""
    with active_types_lock:
        if pokestop_type in active_types:
            # Signal thread to stop
            if pokestop_type in stop_events:
                stop_events[pokestop_type].set()
            
            return jsonify({
                'message': f'Stopping updater for {pokestop_type}',
                'active_types': list(active_types.keys())
            })
        else:
            return jsonify({
                'message': f'{pokestop_type} not active',
                'active_types': list(active_types.keys())
            })

@app.route('/debug/logs')
def debug_logs():
    """Show recent log entries"""
    limit = request.args.get('limit', 100, type=int)
    level = request.args.get('level', '').upper()
    
    logs = list(recent_logs)
    if level:
        logs = [log for log in logs if log['level'] == level]
    
    return jsonify({
        'log_count': len(logs),
        'logs': logs[-limit:]
    })

@app.route('/debug/clear/<pokestop_type>')
def debug_clear_cache(pokestop_type):
    """Clear cache for a specific type"""
    if pokestop_type not in POKESTOP_TYPES:
        return jsonify({'error': f'Invalid type: {pokestop_type}'}), 400
    
    cache_file = get_cache_file(pokestop_type)
    try:
        if os.path.exists(cache_file):
            os.remove(cache_file)
            initialize_cache(pokestop_type)
            return jsonify({'message': f'Cache cleared for {pokestop_type}'})
        else:
            return jsonify({'message': f'No cache to clear for {pokestop_type}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug/stats')
def debug_stats():
    """Aggregated statistics"""
    stats = {
        'types': {},
        'locations': defaultdict(int),
        'total_stops': 0,
        'active_types': list(active_types.keys()),
        'queued_types': list(type_queue)
    }
    
    for pokestop_type in POKESTOP_TYPES.keys():
        cache_file = get_cache_file(pokestop_type)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    stops = data.get('stops', {})
                    
                    type_total = sum(len(s) for s in stops.values())
                    stats['types'][pokestop_type] = type_total
                    stats['total_stops'] += type_total
                    
                    for loc, loc_stops in stops.items():
                        stats['locations'][loc] += len(loc_stops)
            except:
                stats['types'][pokestop_type] = 0
    
    stats['locations'] = dict(stats['locations'])
    return jsonify(stats)

@app.route('/debug/gc')
def debug_gc():
    """Force garbage collection"""
    before = psutil.virtual_memory().used / 1024 / 1024
    collected = gc.collect()
    after = psutil.virtual_memory().used / 1024 / 1024
    
    return jsonify({
        'objects_collected': collected,
        'memory_before_mb': before,
        'memory_after_mb': after,
        'memory_freed_mb': before - after
    })

@app.route('/debug/queue')
def debug_queue():
    """Queue management"""
    action = request.args.get('action', 'view')
    pokestop_type = request.args.get('type', '')
    
    with type_queue_lock:
        if action == 'add' and pokestop_type in POKESTOP_TYPES:
            if pokestop_type not in type_queue:
                type_queue.append(pokestop_type)
                return jsonify({
                    'message': f'Added {pokestop_type} to queue',
                    'queue': list(type_queue)
                })
        elif action == 'remove' and pokestop_type:
            if pokestop_type in type_queue:
                type_queue.remove(pokestop_type)
                return jsonify({
                    'message': f'Removed {pokestop_type} from queue',
                    'queue': list(type_queue)
                })
        elif action == 'clear':
            type_queue.clear()
            return jsonify({
                'message': 'Queue cleared',
                'queue': []
            })
        
        return jsonify({
            'queue': list(type_queue),
            'length': len(type_queue),
            'max_length': MAX_QUEUED_TYPES
        })

# Main route
@app.route('/')
def get_pokestops():
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
    cache_recent = False
    
    if cache_exists:
        cache_age = time.time() - os.path.getmtime(cache_file)
        cache_recent = cache_age < 300  # 5 minutes
    
    with active_types_lock:
        if pokestop_type not in active_types:
            if len(active_types) >= MAX_CONCURRENT_TYPES:
                # Add to queue
                with type_queue_lock:
                    if pokestop_type not in type_queue:
                        type_queue.append(pokestop_type)
                        logger.info(f"📋 Added {pokestop_type} to queue (position {len(type_queue)})")
            else:
                # Start immediately
                if not cache_exists or not cache_recent:
                    logger.info(f"📥 Fetching initial data for {pokestop_type}...")
                    fetch_initial_data(pokestop_type, type_info)
                
                start_type_updater(pokestop_type)
    
    # Load cache
    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        logger.debug(f"📖 Loaded cache for {pokestop_type}")
    except Exception as e:
        logger.error(f"⚠️ Error reading cache for {pokestop_type}: {e}")
        data = {'stops': {location: [] for location in API_ENDPOINTS.keys()}, 'last_updated': 'Unknown'}
    
    # Sort stops
    stops = data.get('stops', {location: [] for location in API_ENDPOINTS.keys()})
    for location in stops:
        stops[location] = sorted(stops[location], key=lambda s: s['remaining_time'], reverse=True)
    
    # Get status info
    with active_types_lock:
        active_types_list = ', '.join(sorted(active_types.keys()))
    with type_queue_lock:
        queued_types_list = ', '.join(list(type_queue)) if type_queue else 'None'
    
    return render_template_string(
        HTML_TEMPLATE,
        stops=stops,
        last_updated=data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        pokestop_type=pokestop_type,
        pokestop_type_display=type_info['display'],
        types=POKESTOP_TYPES.keys(),
        active_types_list=active_types_list or 'None',
        queued_types_list=queued_types_list,
        debug=debug
    )

# HTML template (same as before with minor addition for queue display)
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
            return 120;
        }
        
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
