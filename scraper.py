# scraper.py
import time
import logging
import requests
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from config import config, API_ENDPOINTS
from cache_manager import deduplicate_stops

logger = logging.getLogger(__name__)

def retry_on_failure(max_retries: int = None, delay: int = None):
    """Decorator for retrying failed operations with exponential backoff."""
    max_retries = max_retries or config.RETRY_ATTEMPTS
    delay = delay or config.RETRY_DELAY
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    logger.warning(f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}")
                    
                    if attempt < max_retries - 1:
                        sleep_time = delay * (2 ** attempt)  # Exponential backoff
                        logger.debug(f"Retrying in {sleep_time} seconds...")
                        time.sleep(sleep_time)
                    
            logger.error(f"All {max_retries} attempts failed for {func.__name__}")
            raise last_exception
        return wrapper
    return decorator

class PokeStopScraper:
    """Enhanced scraper with proper error handling and retry logic."""
    
    def __init__(self, pokestop_type: str, type_info: Dict):
        self.pokestop_type = pokestop_type
        self.type_info = type_info
        self.character_ids = type_info['ids']
        self.gender_map = type_info['gender']
        self.display_type = type_info['display']
        self.session = self._create_session()
        
        logger.info(f"Initialized scraper for {self.display_type} ({pokestop_type}) - Character IDs: {self.character_ids}")
    
    def _create_session(self) -> requests.Session:
        """Create requests session with proxy configuration."""
        session = requests.Session()
        
        # Configure proxy if available
        proxy_url = self._get_proxy_url()
        if proxy_url:
            session.proxies = {'http': proxy_url, 'https': proxy_url}
            logger.info("Configured session with proxy")
        
        # Set headers
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        
        return session
    
    def _get_proxy_url(self) -> Optional[str]:
        """Get proxy URL from environment variables."""
        if all([config.NORDVPN_PROXY_HOST, config.NORDVPN_PROXY_USER, config.NORDVPN_PROXY_PASS]):
            return f'socks5://{config.NORDVPN_PROXY_USER}:{config.NORDVPN_PROXY_PASS}@{config.NORDVPN_PROXY_HOST}:1080'
        return None
    
    @retry_on_failure()
    def fetch_location_data(self, location: str, url: str) -> List[Dict]:
        """Fetch and process data for a single location with retry logic."""
        try:
            current_time = time.time()
            params = {'time': int(current_time * 1000)}
            
            logger.debug(f"Fetching data for {location} from {url}")
            
            response = self.session.get(
                url, 
                params=params, 
                timeout=config.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            
            data = response.json()
            stops = self._process_invasions(data, current_time, location)
            
            logger.info(f"✅ Fetched {len(stops)} {self.display_type} ({self.pokestop_type}) PokéStops for {location}")
            return stops
            
        except requests.exceptions.Timeout:
            logger.error(f"Timeout fetching data from {url}")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error for {url}: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error {e.response.status_code} for {url}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error for {url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching data for {location}: {e}")
            raise
    
    def _process_invasions(self, data: Dict, current_time: float, location: str) -> List[Dict]:
        """Process invasion data for the specific pokestop type."""
        meta = data.get('meta', {})
        time_offset = current_time - int(meta.get('time', current_time))
        stops = []
        
        invasions = data.get('invasions', [])
        logger.debug(f"Processing {len(invasions)} invasions for {location}")
        
        for stop in invasions:
            try:
                if self._is_valid_stop(stop, current_time, time_offset):
                    processed_stop = self._create_stop_data(stop, current_time, time_offset, location)
                    stops.append(processed_stop)
            except Exception as e:
                logger.warning(f"Error processing stop in {location}: {e}")
                continue
        
        # Remove duplicates
        unique_stops = deduplicate_stops(stops)
        if len(stops) != len(unique_stops):
            logger.info(f"Removed {len(stops) - len(unique_stops)} duplicate stops in {location}")
        
        return unique_stops
    
    def _is_valid_stop(self, stop: Dict, current_time: float, time_offset: float) -> bool:
        """Check if a stop matches our criteria."""
        # Validate required fields
        if not self._validate_stop_data(stop):
            return False
        
        character_id = stop.get('character')
        grunt_dialogue = stop.get('grunt_dialogue', '').lower()
        remaining_time = stop['invasion_end'] - (current_time - time_offset)
        
        # Check time constraints
        if not (config.MIN_REMAINING_TIME < remaining_time < config.MAX_REMAINING_TIME):
            return False
        
        # Debug logging for specific types
        self._debug_log_character(character_id, grunt_dialogue, remaining_time)
        
        # Check type matching logic
        return (
            character_id in self.character_ids or
            self._is_grunt_match(grunt_dialogue) or
            self._is_type_dialogue_match(grunt_dialogue) or
            self._is_electric_match(character_id, grunt_dialogue)
        )
    
    def _validate_stop_data(self, stop: Dict) -> bool:
        """Validate that stop data has required fields and valid values."""
        required_fields = ['lat', 'lng', 'invasion_end', 'character']
        
        # Check required fields
        for field in required_fields:
            if field not in stop:
                logger.debug(f"Stop missing required field: {field}")
                return False
        
        # Validate coordinate ranges
        try:
            lat, lng = float(stop['lat']), float(stop['lng'])
            if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
                logger.debug(f"Invalid coordinates: lat={lat}, lng={lng}")
                return False
        except (ValueError, TypeError):
            logger.debug(f"Invalid coordinate types: lat={stop['lat']}, lng={stop['lng']}")
            return False
        
        # Validate invasion_end is a reasonable timestamp
        try:
            invasion_end = float(stop['invasion_end'])
            current_time = time.time()
            if invasion_end < current_time or invasion_end > current_time + config.MAX_REMAINING_TIME:
                logger.debug(f"Invalid invasion_end timestamp: {invasion_end}")
                return False
        except (ValueError, TypeError):
            logger.debug(f"Invalid invasion_end type: {stop['invasion_end']}")
            return False
        
        return True
    
    def _debug_log_character(self, character_id: int, grunt_dialogue: str, remaining_time: float):
        """Debug logging for specific character types."""
        # Ghost-type debug logging
        if character_id in [47, 48]:
            logger.debug(f"👻 Ghost Debug: Character ID={character_id}, "
                        f"Dialogue={grunt_dialogue[:50]}, Remaining={remaining_time:.1f}s")
        
        # Water-type debug logging
        if character_id in [38, 39] and self.pokestop_type.startswith('water'):
            expected_gender = self.gender_map.get(character_id, 'Unknown')
            logger.debug(f"💧 Water Debug: {self.pokestop_type} - Character ID={character_id} "
                        f"(Expected: {expected_gender}), Dialogue={grunt_dialogue[:50]}")
    
    def _is_grunt_match(self, grunt_dialogue: str) -> bool:
        """Check for generic grunt matches."""
        return self.pokestop_type.startswith('grunt') and 'grunt' in grunt_dialogue
    
    def _is_type_dialogue_match(self, grunt_dialogue: str) -> bool:
        """Check if dialogue matches the pokestop type."""
        if self.pokestop_type.startswith('grunt'):
            return False
        
        # Handle gender-separated water types
        if self.pokestop_type in ['waterfemale', 'watermale']:
            return 'water' in grunt_dialogue
        
        # Special dialogue patterns
        special_patterns = {
            'ghost': ['ke...ke...', 'ghost'],
            'psychic': ['psychic', 'mind', 'telekinesis'],
            'fighting': ['muscle', 'fighting', 'combat'],
        }
        
        if self.pokestop_type in special_patterns:
            return any(pattern in grunt_dialogue for pattern in special_patterns[self.pokestop_type])
        
        # Default: check if type is in dialogue
        return self.pokestop_type.lower() in grunt_dialogue
    
    def _is_electric_match(self, character_id: int, grunt_dialogue: str) -> bool:
        """Special handling for electric type (IDs 48/49 shared with ghost)."""
        if self.pokestop_type == 'electric' and character_id in [48, 49]:
            electric_keywords = ['shock', 'electric', 'volt', 'charge', 'zap', 'thunder']
            return any(keyword in grunt_dialogue for keyword in electric_keywords)
        return False
    
    def _create_stop_data(self, stop: Dict, current_time: float, time_offset: float, location: str) -> Dict:
        """Create standardized stop data dictionary."""
        character_id = stop.get('character')
        remaining_time = stop['invasion_end'] - (current_time - time_offset)
        
        return {
            'lat': float(stop['lat']),
            'lng': float(stop['lng']),
            'name': stop.get('name', f'Unnamed PokéStop ({location})'),
            'remaining_time': max(0, remaining_time),  # Ensure non-negative
            'character': character_id,
            'type': self.display_type,
            'gender': self.gender_map.get(character_id, 'Unknown'),
            'grunt_dialogue': stop.get('grunt_dialogue', ''),
            'encounter_pokemon_id': stop.get('encounter_pokemon_id'),
            'location': location,
            'scraped_at': datetime.now().isoformat()
        }

class ParallelDataFetcher:
    """Fetch data from multiple locations in parallel."""
    
    def __init__(self, scraper: PokeStopScraper, max_workers: int = 5):
        self.scraper = scraper
        self.max_workers = max_workers
    
    def fetch_all_locations(self) -> Dict[str, List]:
        """Fetch data from all locations concurrently."""
        stops_by_location = {}
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_location = {
                executor.submit(self.scraper.fetch_location_data, location, url): location
                for location, url in API_ENDPOINTS.items()
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_location, timeout=60):
                location = future_to_location[future]
                try:
                    stops_by_location[location] = future.result()
                except Exception as e:
                    logger.error(f"Failed to fetch data for {location}: {e}")
                    stops_by_location[location] = []
        
        return stops_by_location