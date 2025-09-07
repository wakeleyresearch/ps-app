# config.py - Updated for Render deployment
import os
from dataclasses import dataclass
from typing import Dict, List

@dataclass
class AppConfig:
    # Cache settings - optimized for Render's ephemeral storage
    UPDATE_INTERVAL: int = int(os.getenv('UPDATE_INTERVAL', 120))
    MIN_REMAINING_TIME: int = int(os.getenv('MIN_REMAINING_TIME', 180))
    MAX_REMAINING_TIME: int = int(os.getenv('MAX_REMAINING_TIME', 7200))
    CACHE_DIR: str = os.getenv('CACHE_DIR', '/tmp/cache')  # Use /tmp on Render
    
    # API settings
    REQUEST_TIMEOUT: int = int(os.getenv('REQUEST_TIMEOUT', 10))
    RETRY_ATTEMPTS: int = int(os.getenv('RETRY_ATTEMPTS', 3))
    RETRY_DELAY: int = int(os.getenv('RETRY_DELAY', 2))
    
    # Security
    DEBUG_MODE: bool = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
    RATE_LIMIT_PER_HOUR: int = int(os.getenv('RATE_LIMIT_PER_HOUR', 100))
    SECRET_KEY: str = os.getenv('SECRET_KEY')  # Render will generate this
    DEBUG_TOKEN: str = os.getenv('DEBUG_TOKEN', '')
    
    # Logging - optimized for Render
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE: str = os.getenv('LOG_FILE', '/tmp/logs/pokestop_tracker.log')
    LOG_TO_CONSOLE: bool = True  # Always log to console on Render
    
    # Render-specific settings
    PORT: int = int(os.getenv('PORT', 5000))  # Render provides PORT env var
    RENDER_SERVICE_NAME: str = os.getenv('RENDER_SERVICE_NAME', 'pokestops-tracker')
    RENDER_EXTERNAL_URL: str = os.getenv('RENDER_EXTERNAL_URL', '')
    
    # Proxy settings
    NORDVPN_PROXY_HOST: str = os.getenv('NORDVPN_PROXY_HOST', '')
    NORDVPN_PROXY_USER: str = os.getenv('NORDVPN_PROXY_USER', '')
    NORDVPN_PROXY_PASS: str = os.getenv('NORDVPN_PROXY_PASS', '')

# API endpoints - REORDERED: NYC > Sydney > London > Singapore > Vancouver
API_ENDPOINTS = {
    'NYC': 'https://nycpokemap.com/pokestop.php',
    'Sydney': 'https://sydneypogomap.com/pokestop.php',
    'London': 'https://londonpogomap.com/pokestop.php',
    'Singapore': 'https://sgpokemap.com/pokestop.php',
    'Vancouver': 'https://vanpokemap.com/pokestop.php'
}

# Grunt type configuration with gender-separated water types
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
    # Gender-separated water types with proper button labels
    'waterfemale': {'ids': [38], 'gender': {38: 'Female'}, 'display': 'Water', 'button_label': 'Water (Female)'},
    'watermale': {'ids': [39], 'gender': {39: 'Male'}, 'display': 'Water', 'button_label': 'Water (Male)'},
    'electric': {'ids': [48, 49], 'gender': {49: 'Male', 48: 'Female'}, 'display': 'Electric', 'button_label': 'Electric'},
    'ghost': {'ids': [47, 48], 'gender': {47: 'Male', 48: 'Female'}, 'display': 'Ghost', 'button_label': 'Ghost'}
}

# Load config
config = AppConfig()

# Render-specific validation
if not config.SECRET_KEY:
    if not config.DEBUG_MODE:
        raise ValueError("SECRET_KEY must be set in production. Render should auto-generate this.")
    else:
        # Use a dev key for local testing
        config.SECRET_KEY = 'dev-key-for-local-testing-only'

# Create required directories with error handling for Render
try:
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
except PermissionError:
    # On Render, we might not have write permissions everywhere
    # Fall back to /tmp which should always be writable
    config.CACHE_DIR = '/tmp/cache'
    config.LOG_FILE = '/tmp/pokestop_tracker.log'
    os.makedirs(config.CACHE_DIR, exist_ok=True)

# Render deployment info
def get_deployment_info():
    """Get information about the current Render deployment."""
    return {
        'service_name': config.RENDER_SERVICE_NAME,
        'external_url': config.RENDER_EXTERNAL_URL,
        'port': config.PORT,
        'cache_dir': config.CACHE_DIR,
        'log_file': config.LOG_FILE,
        'debug_mode': config.DEBUG_MODE
    }