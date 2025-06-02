from flask import Flask, render_template_string
import requests
from datetime import datetime
import time
import threading
import json
import os

app = Flask(__name__)

# Cache file path
CACHE_FILE = '/app/fairy_pokestops.json'  # Render's writable directory
# Cache update interval (seconds)
UPDATE_INTERVAL = 120
# Minimum remaining time for PokéStops (seconds)
MIN_REMAINING_TIME = 180

# API endpoints for each location
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php'
}

# Initialize cache
if not os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'w') as f:
        json.dump({
            'stops': {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []},
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }, f)

def update_cache():
    while True:
        try:
            stops_by_location = {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []}
            current_time = int(time.time())

            for location, url in API_ENDPOINTS.items():
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
                    response = requests.get(url, params={'time': int(time.time() * 1000)}, headers=headers, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    meta = data.get('meta', {})
                    time_offset = current_time - int(meta.get('time', current_time))

                    fairy_stops = [
                        {
                            'lat': stop['lat'],
                            'lng': stop['lng'],
                            'name': stop['name'] or f'Unnamed PokéStop ({location})',
                            'remaining_time': stop['invasion_end'] - (current_time - time_offset)
                        }
                        for stop in data.get('invasions', [])
                        if stop.get('character') in [14, 15] and (stop['invasion_end'] - (current_time - time_offset)) > MIN_REMAINING_TIME
                    ]
                    stops_by_location[location] = fairy_stops
                    print(f"✅ Fetched {len(fairy_stops)} Fairy-type PokéStops for {location}.")
                except Exception as e:
                    print(f"❌ Error fetching data for {location}: {e}")

            with open(CACHE_FILE, 'w') as f:
                json.dump({
                    'stops': stops_by_location,
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }, f)
            print(f"✅ Cache updated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.")
        except Exception as e:
            print(f"❌ Error updating cache: {e}")
        time.sleep(UPDATE_INTERVAL)

# Start cache update thread
threading.Thread(target=update_cache, daemon=True).start()

# HTML template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Fairy-Type PokéStops</title>
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
    <h1>Fairy-Type PokéStops</h1>
    <p>Last updated: {{ last_updated }}</p>
    <p>Updates every 2 minutes. Only PokéStops with more than 3 minutes remaining are shown.</p>
    {% for location, stops in stops.items() %}
        <h2>{{ location }}</h2>
        {% if stops %}
            <ul>
            {% for stop in stops %}
                <li>{{ stop.name }} (<a href="https://maps.google.com/?q={{ stop.lat }},{{ stop.lng }}">{{ stop.lat }}, {{ stop.lng }}</a>) - {{ stop.remaining_time // 60 }} min {{ stop.remaining_time % 60 }} sec remaining</li>
            {% endfor %}
            </ul>
        {% else %}
            <p class="no-stops">No Fairy-type PokéStops found in {{ location }}.</p>
        {% endif %}
    {% endfor %}
</body>
</html>
"""

@app.route('/')
def get_fairy_pokestops():
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
        return render_template_string(
            HTML_TEMPLATE,
            stops=data.get('stops', {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []}),
            last_updated=data.get('last_updated', 'Unknown')
        )
    except Exception:
        # Fallback to fetching data if cache fails
        try:
            stops_by_location = {'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []}
            current_time = int(time.time())
            for location, url in API_ENDPOINTS.items():
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
                    response = requests.get(url, params={'time': int(time.time() * 1000)}, headers=headers, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    meta = data.get('meta', {})
                    time_offset = current_time - int(meta.get('time', current_time))
                    fairy_stops = [
                        {
                            'lat': stop['lat'],
                            'lng': stop['lng'],
                            'name': stop['name'] or f'Unnamed PokéStop ({location})',
                            'remaining_time': stop['invasion_end'] - (current_time - time_offset)
                        }
                        for stop in data.get('invasions', [])
                        if stop.get('character') in [14, 15] and (stop['invasion_end'] - (current_time - time_offset)) > MIN_REMAINING_TIME
                    ]
                    stops_by_location[location] = fairy_stops
                except:
                    pass
            return render_template_string(
                HTML_TEMPLATE,
                stops=stops_by_location,
                last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
        except:
            return render_template_string(
                HTML_TEMPLATE,
                stops={'NYC': [], 'Vancouver': [], 'Singapore': [], 'London': []},
                last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))  # Dynamic port for Render
    app.run(host='0.0.0.0', port=port, debug=False)  # Disable debug for production