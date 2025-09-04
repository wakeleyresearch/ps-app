# app.py
import os
import logging
import threading
import time
from datetime import datetime
from io import BytesIO
from typing import Dict, Set
import xml.etree.ElementTree as ET

from flask import Flask, render_template_string, request, send_file, jsonify, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from markupsafe import escape

# Import our modules
from config import config, POKESTOP_TYPES, API_ENDPOINTS
from cache_manager import cache_manager
from scraper import PokeStopScraper, ParallelDataFetcher

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[f"{config.RATE_LIMIT_PER_HOUR} per hour", "20 per minute"],
    storage_uri="memory://"
)

# Global managers
active_types: Set[str] = set(['fairy'])
active_types_lock = threading.Lock()

class TypeManager:
    """Utility class to manage pokestop types and their relationships."""
    
    @staticmethod
    def get_type_groups() -> Dict[str, list]:
        """Group types for better UI organization."""
        groups = {
            'grunts': [],
            'regular': [],
            'water': [],
            'special': []
        }
        
        for type_name in POKESTOP_TYPES.keys():
            if type_name.startswith('grunt'):
                groups['grunts'].append(type_name)
            elif type_name.startswith('water'):
                groups['water'].append(type_name)
            elif type_name in ['electric', 'ghost']:
                groups['special'].append(type_name)
            else:
                groups['regular'].append(type_name)
        
        return groups
    
    @staticmethod
    def get_display_name(type_name: str) -> str:
        """Get a user-friendly display name for a type."""
        type_info = POKESTOP_TYPES.get(type_name, {})
        base_display = type_info.get('display', type_name.capitalize())
        
        if type_name.endswith('male'):
            return f"{base_display} (Male)"
        elif type_name.endswith('female'):
            return f"{base_display} (Female)"
        
        return base_display
    
    @staticmethod
    def validate_type(type_name: str) -> bool:
        """Validate if a type name exists."""
        return type_name in POKESTOP_TYPES

def validate_input(pokestop_type: str) -> str:
    """Validate and sanitize pokestop type input."""
    if not pokestop_type or not isinstance(pokestop_type, str):
        return 'fairy'
    
    pokestop_type = pokestop_type.lower().strip()
    
    if not TypeManager.validate_type(pokestop_type):
        logger.warning(f"Invalid pokestop type requested: {pokestop_type}")
        return 'fairy'
    
    return pokestop_type

def update_cache_worker(pokestop_type: str, type_info: Dict):
    """Worker function to update cache for a single type."""
    scraper = PokeStopScraper(pokestop_type, type_info)
    fetcher = ParallelDataFetcher(scraper)
    
    while True:
        try:
            logger.info(f"Starting cache update for {pokestop_type}")
            
            # Fetch data from all locations
            stops_by_location = fetcher.fetch_all_locations()
            
            # Prepare cache data
            cache_data = {
                'stops': stops_by_location,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            # Write to cache
            success = cache_manager.write_cache(pokestop_type, cache_data)
            if success:
                total_stops = sum(len(stops) for stops in stops_by_location.values())
                logger.info(f"✅ Cache updated for {pokestop_type}: {total_stops} total stops")
            else:
                logger.error(f"❌ Failed to write cache for {pokestop_type}")
                
        except Exception as e:
            logger.error(f"❌ Error updating cache for {pokestop_type}: {e}")
        
        # Wait before next update
        time.sleep(config.UPDATE_INTERVAL)

def start_cache_thread(pokestop_type: str):
    """Start cache update thread for a pokestop type."""
    with active_types_lock:
        if pokestop_type not in active_types:
            type_info = POKESTOP_TYPES[pokestop_type]
            
            # Initialize cache
            cache_manager.initialize_cache(pokestop_type)
            
            # Start background thread
            thread = threading.Thread(
                target=update_cache_worker,
                args=(pokestop_type, type_info),
                daemon=True,
                name=f"cache-worker-{pokestop_type}"
            )
            thread.start()
            
            active_types.add(pokestop_type)
            logger.info(f"🛠️ Started cache thread for {pokestop_type}")

# HTML Template with improved UX
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{{ display_name }} PokéStops</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="120">
    <style>
        :root {
            --primary-color: #007bff;
            --secondary-color: #6c757d;
            --success-color: #28a745;
            --danger-color: #dc3545;
            --warning-color: #ffc107;
            --info-color: #17a2b8;
            --light-color: #f8f9fa;
            --dark-color: #343a40;
        }
        
        * { box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f6fa;
            color: #2f3542;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        h1 {
            color: var(--dark-color);
            margin-bottom: 10px;
            font-size: 2rem;
        }
        
        h2 {
            color: var(--secondary-color);
            margin-top: 30px;
            margin-bottom: 15px;
            font-size: 1.5rem;
            border-bottom: 2px solid var(--light-color);
            padding-bottom: 5px;
        }
        
        .info-bar {
            background: white;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .type-selector {
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .type-group {
            margin: 15px 0;
        }
        
        .type-group label {
            display: block;
            font-weight: 600;
            margin-bottom: 8px;
            color: var(--dark-color);
        }
        
        .type-link {
            display: inline-block;
            padding: 8px 12px;
            margin: 3px;
            background: var(--light-color);
            border: 1px solid #dee2e6;
            border-radius: 20px;
            text-decoration: none;
            color: var(--dark-color);
            font-size: 0.9rem;
            transition: all 0.2s ease;
        }
        
        .type-link:hover {
            background: #e9ecef;
            transform: translateY(-1px);
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-decoration: none;
        }
        
        .type-link.active {
            background: var(--primary-color);
            color: white;
            border-color: var(--primary-color);
        }
        
        .gender-badge {
            font-size: 0.75rem;
            padding: 2px 6px;
            border-radius: 10px;
            margin-left: 5px;
            font-weight: 500;
        }
        
        .gender-male {
            background: #cce5ff;
            color: #0056b3;
        }
        
        .gender-female {
            background: #ffe6f2;
            color: #b91372;
        }
        
        .actions {
            background: white;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .btn {
            display: inline-block;
            padding: 8px 16px;
            margin: 5px;
            border: none;
            border-radius: 5px;
            text-decoration: none;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .btn-primary {
            background: var(--primary-color);
            color: white;
        }
        
        .btn-secondary {
            background: var(--secondary-color);
            color: white;
        }
        
        .btn:hover {
            transform: translateY(-1px);
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            text-decoration: none;
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .stats {
            background: var(--info-color);
            color: white;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
        }
        
        .loading {
            display: none;
            color: var(--warning-color);
            font-style: italic;
            margin-left: 10px;
        }
        
        .error-message {
            color: var(--danger-color);
            background: #f8d7da;
            border: 1px solid #f5c6cb;
            padding: 12px;
            border-radius: 5px;
            margin: 10px 0;
        }
        
        .success-message {
            color: var(--success-color);
            background: #d4edda;
            border: 1px solid #c3e6cb;
            padding: 12px;
            border-radius: 5px;
            margin: 10px 0;
        }
        
        .location-section {
            background: white;
            margin: 20px 0;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        
        .location-header {
            background: var(--light-color);
            padding: 15px 20px;
            border-bottom: 1px solid #dee2e6;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .stop-list {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        
        .stop-item {
            padding: 15px 20px;
            border-bottom: 1px solid #f8f9fa;
            transition: background-color 0.2s ease;
        }
        
        .stop-item:hover {
            background: #f8f9fa;
        }
        
        .stop-item:last-child {
            border-bottom: none;
        }
        
        .stop-name {
            font-weight: 500;
            color: var(--dark-color);
        }
        
        .stop-coords {
            color: var(--primary-color);
            text-decoration: none;
            font-family: monospace;
        }
        
        .stop-coords:hover {
            text-decoration: underline;
        }
        
        .stop-time {
            color: var(--success-color);
            font-weight: 500;
        }
        
        .debug {
            font-size: 0.8rem;
            color: var(--secondary-color);
            margin-top: 5px;
            font-family: monospace;
        }
        
        .no-stops {
            padding: 40px 20px;
            text-align: center;
            color: var(--secondary-color);
        }
        
        @media (max-width: 768px) {
            body { padding: 10px; }
            h1 { font-size: 1.5rem; }
            .type-link { font-size: 0.8rem; padding: 6px 10px; }
            .stop-item { padding: 12px 15px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>{{ display_name }} PokéStops</h1>
        
        <div class="info-bar">
            <strong>Last updated:</strong> {{ last_updated }}
            <span class="loading">Updating...</span>
            <br>
            <small>Updates every 2 minutes. Only PokéStops with more than 3 minutes remaining are shown.</small>
        </div>
        
        <div class="type-selector">
            <h3>Switch Type</h3>
            
            <div class="type-group">
                <label>🛡️ Grunts</label>
                {% for type_name in type_groups.grunts %}
                <a href="?type={{ type_name }}{% if debug %}&debug=true{% endif %}" 
                   class="type-link {{ 'active' if pokestop_type == type_name else '' }}">
                    Grunt
                    <span class="gender-badge gender-{{ 'male' if 'male' in type_name else 'female' }}">
                        {{ 'Male' if 'male' in type_name else 'Female' }}
                    </span>
                </a>
                {% endfor %}
            </div>
            
            <div class="type-group">
                <label>⚡ Regular Types</label>
                {% for type_name in type_groups.regular %}
                <a href="?type={{ type_name }}{% if debug %}&debug=true{% endif %}" 
                   class="type-link {{ 'active' if pokestop_type == type_name else '' }}">
                    {{ type_name.capitalize() }}
                </a>
                {% endfor %}
            </div>
            
            <div class="type-group">
                <label>💧 Water Types</label>
                {% for type_name in type_groups.water %}
                <a href="?type={{ type_name }}{% if debug %}&debug=true{% endif %}" 
                   class="type-link {{ 'active' if pokestop_type == type_name else '' }}">
                    Water
                    <span class="gender-badge gender-{{ 'male' if 'male' in type_name else 'female' }}">
                        {{ 'Male' if 'male' in type_name else 'Female' }}
                    </span>
                </a>
                {% endfor %}
            </div>
            
            <div class="type-group">
                <label>🔮 Special Types</label>
                {% for type_name in type_groups.special %}
                <a href="?type={{ type_name }}{% if debug %}&debug=true{% endif %}" 
                   class="type-link {{ 'active' if pokestop_type == type_name else '' }}">
                    {{ type_name.capitalize() }}
                </a>
                {% endfor %}
            </div>
        </div>
        
        <div class="actions">
            <a href="/download_gpx?type={{ pokestop_type }}" target="_blank" class="btn btn-primary">
                📱 Download GPX (10+ min remaining)
            </a>
            {% if debug %}
                <a href="?type={{ pokestop_type }}" class="btn btn-secondary">Hide Debug Info</a>
            {% else %}
                <a href="?type={{ pokestop_type }}&debug=true" class="btn btn-secondary">Show Debug Info</a>
            {% endif %}
        </div>
        
        {% set total_stops = stops.values() | map('length') | sum %}
        {% if total_stops > 0 %}
        <div class="stats">
            <strong>📊 Statistics:</strong> 
            {{ total_stops }} total stops found across all locations
            <span class="loading">Updating...</span>
        </div>
        {% endif %}
        
        {% for location, location_stops in stops.items() %}
        <div class="location-section">
            <div class="location-header">
                <h2>{{ location }}</h2>
                <button id="sort-btn-{{ location }}" onclick="toggleSort('{{ location }}')" class="btn btn-secondary">
                    Sort by Nearest Neighbor
                </button>
            </div>
            
            {% if location_stops %}
                <ul id="stops-list-{{ location }}" class="stop-list">
                    {% for stop in location_stops %}
                        <li class="stop-item">
                            <div class="stop-name">
                                {{ stop.type }}
                                <span class="gender-badge gender-{{ stop.gender.lower() }}">{{ stop.gender }}</span>
                                {{ stop.name | e }}
                            </div>
                            <div>
                                📍 <a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}" 
                                     target="_blank" class="stop-coords">{{ "%.6f"|format(stop.lat) }}, {{ "%.6f"|format(stop.lng) }}</a>
                                ⏰ <span class="stop-time">{{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining</span>
                            </div>
                            {% if debug %}
                                <div class="debug">
                                    Character: {{ stop.character }}, 
                                    Dialogue: {{ (stop.grunt_dialogue or 'N/A')[:100] | e }}, 
                                    Encounter ID: {{ stop.encounter_pokemon_id or 'N/A' }}
                                </div>
                            {% endif %}
                        </li>
                    {% endfor %}
                </ul>
            {% else %}
                <div class="no-stops">
                    <p>No {{ display_name }} PokéStops found in {{ location }}.</p>
                </div>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    
    <script>
        const PokeStopApp = {
            stopsData: {
                {% for location in stops.keys() %}
                '{{ location }}': {{ stops[location] | tojson }},
                {% endfor %}
            },
            sortModes: {},
            isDebug: {{ debug | tojson }},
            
            init() {
                this.setupAutoRefresh();
                this.setupErrorHandling();
                this.initializeSortModes();
            },
            
            setupAutoRefresh() {
                setInterval(() => {
                    this.showLoading(true);
                    setTimeout(() => this.showLoading(false), 2000);
                }, 120000);
            },
            
            setupErrorHandling() {
                window.addEventListener('error', (e) => {
                    this.showError('Something went wrong. Please refresh the page.');
                    console.error('Error:', e);
                });
            },
            
            initializeSortModes() {
                Object.keys(this.stopsData).forEach(location => {
                    this.sortModes[location] = 'time';
                });
            },
            
            showLoading(show) {
                document.querySelectorAll('.loading').forEach(el => {
                    el.style.display = show ? 'inline' : 'none';
                });
            },
            
            showError(message) {
                const errorDiv = document.createElement('div');
                errorDiv.className = 'error-message';
                errorDiv.textContent = message;
                document.body.insertBefore(errorDiv, document.body.firstChild);
                setTimeout(() => errorDiv.remove(), 5000);
            },
            
            distance(a, b) {
                const R = 6371;
                const dLat = (b.lat - a.lat) * Math.PI / 180;
                const dLon = (b.lng - a.lng) * Math.PI / 180;
                const lat1 = a.lat * Math.PI / 180;
                const lat2 = b.lat * Math.PI / 180;
                const x = Math.sin(dLat / 2) * Math.sin(dLat / 2) + 
                         Math.sin(dLon / 2) * Math.sin(dLon / 2) * Math.cos(lat1) * Math.cos(lat2);
                const c = 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
                return R * c;
            },
            
            nearestNeighbor(points) {
                if (points.length <= 1) return points;
                points.sort((a, b) => b.remaining_time - a.remaining_time);
                let ordered = [points.shift()];
                
                while (points.length > 0) {
                    let last = ordered[ordered.length - 1];
                    let minDist = Infinity;
                    let closestIdx = -1;
                    
                    for (let i = 0; i < points.length; i++) {
                        let dist = this.distance(last, points[i]);
                        if (dist < minDist) {
                            minDist = dist;
                            closestIdx = i;
                        }
                    }
                    ordered.push(points.splice(closestIdx, 1)[0]);
                }
                return ordered;
            },
            
            renderStops(location, stops) {
                const ul = document.getElementById('stops-list-' + location);
                ul.innerHTML = '';
                
                stops.forEach(stop => {
                    const li = document.createElement('li');
                    li.className = 'stop-item';
                    
                    let html = `
                        <div class="stop-name">
                            ${stop.type}
                            <span class="gender-badge gender-${stop.gender.toLowerCase()}">${stop.gender}</span>
                            ${this.escapeHtml(stop.name)}
                        </div>
                        <div>
                            📍 <a href="https://maps.google.com/?q=${stop.lat},${stop.lng}" 
                                 target="_blank" class="stop-coords">${stop.lat.toFixed(6)}, ${stop.lng.toFixed(6)}</a>
                            ⏰ <span class="stop-time">${Math.floor(stop.remaining_time / 60)} min ${stop.remaining_time % 60} sec remaining</span>
                        </div>
                    `;
                    
                    if (this.isDebug) {
                        html += `
                            <div class="debug">
                                Character: ${stop.character}, 
                                Dialogue: ${this.escapeHtml((stop.grunt_dialogue || 'N/A').substring(0, 100))}, 
                                Encounter ID: ${stop.encounter_pokemon_id || 'N/A'}
                            </div>
                        `;
                    }
                    
                    li.innerHTML = html;
                    ul.appendChild(li);
                });
            },
            
            escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            },
            
            toggleSort(location) {
                this.sortModes[location] = this.sortModes[location] === 'nearest' ? 'time' : 'nearest';
                
                const button = document.getElementById('sort-btn-' + location);
                button.disabled = true;
                button.textContent = 'Processing...';
                
                setTimeout(() => {
                    let stops = [...this.stopsData[location]];
                    
                    if (this.sortModes[location] === 'nearest') {
                        stops = this.nearestNeighbor(stops);
                        button.textContent = 'Sort by Time Remaining';
                    } else {
                        stops.sort((a, b) => b.remaining_time - a.remaining_time);
                        button.textContent = 'Sort by Nearest Neighbor';
                    }
                    
                    this.renderStops(location, stops);
                    button.disabled = false;
                }, 100);
            }
        };
        
        // Global function for onclick handlers
        function toggleSort(location) {
            PokeStopApp.toggleSort(location);
        }
        
        // Initialize when DOM is ready
        document.addEventListener('DOMContentLoaded', () => PokeStopApp.init());
    </script>
</body>
</html>
"""

# Routes
@app.route('/')
@limiter.limit("10 per minute")
def get_pokestops():
    """Main route to display pokestops with input validation and error handling."""
    try:
        # Validate inputs
        pokestop_type = validate_input(request.args.get('type', 'fairy'))
        debug = request.args.get('debug', 'false').lower() == 'true'
        
        # Start cache thread if needed
        start_cache_thread(pokestop_type)
        
        # Read cache data
        try:
            data = cache_manager.read_cache(pokestop_type)
        except Exception as e:
            logger.error(f"Error reading cache for {pokestop_type}: {e}")
            data = cache_manager._get_empty_cache()
        
        # Sort stops by remaining_time descending
        stops = data.get('stops', {})
        for location in stops:
            stops[location] = sorted(
                stops[location], 
                key=lambda s: s.get('remaining_time', 0), 
                reverse=True
            )
        
        # Prepare template data
        template_data = {
            'stops': stops,
            'last_updated': data.get('last_updated', 'Unknown'),
            'pokestop_type': pokestop_type,
            'debug': debug,
            'type_groups': TypeManager.get_type_groups(),
            'display_name': TypeManager.get_display_name(pokestop_type)
        }
        
        return render_template_string(HTML_TEMPLATE, **template_data)
        
    except Exception as e:
        logger.error(f"Error in get_pokestops: {e}")
        # Return safe fallback
        fallback_data = {
            'stops': {location: [] for location in API_ENDPOINTS.keys()},
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'pokestop_type': 'fairy',
            'debug': False,
            'type_groups': TypeManager.get_type_groups(),
            'display_name': 'Fairy'
        }
        return render_template_string(HTML_TEMPLATE, **fallback_data)

@app.route('/download_gpx')
@limiter.limit("5 per minute")
def download_gpx():
    """Download GPX file of pokestops with over 10 minutes remaining."""
    try:
        pokestop_type = validate_input(request.args.get('type', 'fairy'))
        
        # Read cache
        data = cache_manager.read_cache(pokestop_type)
        
        # Filter stops with remaining_time > 600 seconds (10 minutes)
        filtered_stops = []
        for location, stops in data['stops'].items():
            for stop in stops:
                if stop.get('remaining_time', 0) > 600:
                    filtered_stops.append(stop)
        
        # Generate GPX
        gpx = ET.Element('gpx', version="1.1", creator="PokeStops Tracker")
        metadata = ET.SubElement(gpx, 'metadata')
        name = ET.SubElement(metadata, 'name')
        name.text = f"{TypeManager.get_display_name(pokestop_type)} PokéStops"
        
        for stop in filtered_stops:
            try:
                wpt = ET.SubElement(gpx, 'wpt', 
                                  lat=str(stop['lat']), 
                                  lon=str(stop['lng']))
                name_elem = ET.SubElement(wpt, 'name')
                name_elem.text = escape(stop.get('name', 'Unknown PokéStop'))
                
                desc = ET.SubElement(wpt, 'desc')
                desc.text = f"{stop.get('type', 'Unknown')} ({stop.get('gender', 'Unknown')}) - {stop.get('remaining_time', 0)//60} min remaining"
            except (KeyError, ValueError) as e:
                logger.warning(f"Skipping invalid stop data: {e}")
                continue
        
        gpx_str = ET.tostring(gpx, encoding='unicode')
        
        return send_file(
            BytesIO(gpx_str.encode('utf-8')),
            mimetype='application/gpx+xml',
            as_attachment=True,
            download_name=f'{pokestop_type}_pokestops.gpx'
        )
        
    except Exception as e:
        logger.error(f"Error generating GPX: {e}")
        abort(500)

@app.route('/health')
def health_check():
    """Health check endpoint for load balancers."""
    try:
        # Basic health checks
        cache_stats = cache_manager.get_cache_stats('fairy')
        
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'active_cache_threads': len(active_types),
            'cache_available': cache_stats.get('exists', False),
            'version': '2.0.0'
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@app.route('/debug_api')
@limiter.limit("2 per minute")
def debug_api():
    """Secured debug endpoint for inspecting raw API data."""
    if not config.DEBUG_MODE:
        abort(404)
    
    # Require debug token in production-like environments
    if config.DEBUG_TOKEN and request.args.get('token') != config.DEBUG_TOKEN:
        abort(403)
    
    location = request.args.get('location', 'London')
    if location not in API_ENDPOINTS:
        return jsonify({'error': 'Invalid location'}), 400
    
    try:
        import requests
        url = API_ENDPOINTS[location]
        response = requests.get(
            url, 
            params={'time': int(time.time() * 1000)}, 
            timeout=config.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Debug API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/stats')
@limiter.limit("10 per minute")
def get_stats():
    """Get statistics about cache and active threads."""
    try:
        stats = {}
        for pokestop_type in active_types:
            stats[pokestop_type] = cache_manager.get_cache_stats(pokestop_type)
        
        return jsonify({
            'active_types': list(active_types),
            'cache_stats': stats,
            'total_active_threads': len(active_types)
        })
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Rate limit exceeded', 'retry_after': e.retry_after}), 429

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

# Initialize default cache thread
if __name__ == '__main__':
    logger.info("Starting PokeStops Tracker v2.0 on Render")
    
    # Start default fairy type cache
    start_cache_thread('fairy')
    
    # Run app with Render-specific configuration
    app.run(
        host='0.0.0.0', 
        port=config.PORT,  # Use Render's PORT environment variable
        debug=config.DEBUG_MODE,
        threaded=True
    )