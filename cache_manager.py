# cache_manager.py - Optimized for Render's ephemeral storage
import gzip
import json
import os
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from threading import Lock
from config import config, API_ENDPOINTS

logger = logging.getLogger(__name__)

class RenderCacheManager:
    """Cache manager optimized for Render's ephemeral storage."""
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_locks = {}
        self.master_lock = Lock()
        self.cache_memory = {}  # In-memory backup for ephemeral storage
        self.last_fetch_times = {}  # Track when data was last fetched
        
        # Ensure cache directory exists
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            logger.info(f"Cache directory initialized at {self.cache_dir}")
        except PermissionError as e:
            logger.warning(f"Could not create cache directory {self.cache_dir}: {e}")
            # Fall back to in-memory only
            self.cache_dir = None
    
    def _get_cache_lock(self, pokestop_type: str) -> Lock:
        """Get or create a lock for the specific pokestop type."""
        with self.master_lock:
            if pokestop_type not in self.cache_locks:
                self.cache_locks[pokestop_type] = Lock()
            return self.cache_locks[pokestop_type]
    
    def get_cache_file(self, pokestop_type: str) -> Optional[str]:
        """Return cache file path for the given type, or None if no file storage."""
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, f'pokestops_{pokestop_type}.json.gz')
    
    def read_cache(self, pokestop_type: str) -> Dict:
        """Read cache with fallback to in-memory storage."""
        cache_lock = self._get_cache_lock(pokestop_type)
        
        with cache_lock:
            # Try in-memory cache first (faster and always available)
            if pokestop_type in self.cache_memory:
                cache_age = datetime.now() - self.last_fetch_times.get(pokestop_type, datetime.min)
                # If cache is less than 5 minutes old, use it
                if cache_age < timedelta(minutes=5):
                    logger.debug(f"Using in-memory cache for {pokestop_type} (age: {cache_age})")
                    return self.cache_memory[pokestop_type]
            
            # Try file cache if available
            cache_file = self.get_cache_file(pokestop_type)
            if cache_file and os.path.exists(cache_file):
                try:
                    with gzip.open(cache_file, 'rt', encoding='utf-8') as f:
                        data = json.load(f)
                        # Update in-memory cache
                        self.cache_memory[pokestop_type] = data
                        self.last_fetch_times[pokestop_type] = datetime.now()
                        logger.debug(f"Loaded cache for {pokestop_type} from file")
                        return data
                except Exception as e:
                    logger.warning(f"Failed to read cache file for {pokestop_type}: {e}")
            
            # Return empty cache if nothing found
            logger.info(f"No cache found for {pokestop_type}, returning empty cache")
            return self._get_empty_cache()
    
    def write_cache(self, pokestop_type: str, data: Dict) -> bool:
        """Write cache to both memory and file (if possible)."""
        cache_lock = self._get_cache_lock(pokestop_type)
        
        with cache_lock:
            try:
                # Validate data structure
                if not self._validate_cache_data(data):
                    logger.error(f"Invalid cache data structure for {pokestop_type}")
                    return False
                
                # Add metadata
                data['cache_metadata'] = {
                    'write_time': datetime.now().isoformat(),
                    'data_hash': self._calculate_data_hash(data['stops']),
                    'pokestop_type': pokestop_type,
                    'storage_type': 'render_ephemeral'
                }
                
                # Always update in-memory cache first (most reliable on Render)
                self.cache_memory[pokestop_type] = data.copy()
                self.last_fetch_times[pokestop_type] = datetime.now()
                
                # Try to write to file if possible
                cache_file = self.get_cache_file(pokestop_type)
                if cache_file:
                    try:
                        temp_file = cache_file + '.tmp'
                        with gzip.open(temp_file, 'wt', encoding='utf-8') as f:
                            json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
                        
                        # Atomic move
                        os.rename(temp_file, cache_file)
                        logger.debug(f"Cache written to file for {pokestop_type}")
                    except Exception as e:
                        logger.warning(f"Could not write cache file for {pokestop_type}: {e}")
                        # Clean up temp file
                        if os.path.exists(temp_file):
                            try:
                                os.remove(temp_file)
                            except OSError:
                                pass
                
                logger.info(f"Cache updated for {pokestop_type} (in-memory + file backup)")
                return True
                
            except Exception as e:
                logger.error(f"Failed to write cache for {pokestop_type}: {e}")
                return False
    
    def _validate_cache_data(self, data: Dict) -> bool:
        """Validate cache data structure."""
        required_keys = ['stops', 'last_updated']
        if not all(key in data for key in required_keys):
            return False
        
        if not isinstance(data['stops'], dict):
            return False
        
        # Validate that all API endpoints are present
        expected_locations = set(API_ENDPOINTS.keys())
        actual_locations = set(data['stops'].keys())
        if not expected_locations.issubset(actual_locations):
            logger.debug(f"Some locations missing in cache: {expected_locations - actual_locations}")
        
        return True
    
    def _calculate_data_hash(self, data: Dict) -> str:
        """Calculate hash of data to detect changes."""
        data_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.md5(data_str.encode()).hexdigest()
    
    def _get_empty_cache(self) -> Dict:
        """Return empty cache structure."""
        return {
            'stops': {location: [] for location in API_ENDPOINTS.keys()},
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def initialize_cache(self, pokestop_type: str) -> bool:
        """Initialize cache for the given type."""
        try:
            # Check if we already have cache in memory or file
            existing_cache = self.read_cache(pokestop_type)
            
            # If cache is empty or very old, initialize with empty structure
            if not existing_cache.get('stops') or not any(
                stops for stops in existing_cache['stops'].values()
            ):
                empty_cache = self._get_empty_cache()
                success = self.write_cache(pokestop_type, empty_cache)
                if success:
                    logger.info(f"Initialized cache for {pokestop_type}")
                return success
            else:
                logger.debug(f"Cache already exists for {pokestop_type}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to initialize cache for {pokestop_type}: {e}")
            return False
    
    def get_cache_stats(self, pokestop_type: str) -> Dict:
        """Get statistics about a cache."""
        stats = {
            'pokestop_type': pokestop_type,
            'exists': False,
            'in_memory': pokestop_type in self.cache_memory,
            'storage_type': 'render_ephemeral'
        }
        
        try:
            # Check in-memory cache
            if pokestop_type in self.cache_memory:
                data = self.cache_memory[pokestop_type]
                total_stops = sum(len(stops) for stops in data['stops'].values())
                last_fetch = self.last_fetch_times.get(pokestop_type)
                
                stats.update({
                    'exists': True,
                    'total_stops': total_stops,
                    'locations': list(data['stops'].keys()),
                    'last_updated': data.get('last_updated', 'Unknown'),
                    'last_fetch_time': last_fetch.isoformat() if last_fetch else 'Unknown',
                    'data_hash': data.get('cache_metadata', {}).get('data_hash', 'Unknown')
                })
            
            # Check file cache if available
            cache_file = self.get_cache_file(pokestop_type)
            if cache_file and os.path.exists(cache_file):
                file_stat = os.stat(cache_file)
                stats.update({
                    'file_exists': True,
                    'file_size': file_stat.st_size,
                    'file_modified': datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                })
            else:
                stats['file_exists'] = False
                
        except Exception as e:
            logger.error(f"Error getting cache stats for {pokestop_type}: {e}")
            stats['error'] = str(e)
        
        return stats
    
    def cleanup_old_memory_caches(self, max_age_minutes: int = 30) -> int:
        """Clean up old in-memory caches to prevent memory leaks."""
        cleaned = 0
        current_time = datetime.now()
        
        try:
            with self.master_lock:
                expired_types = []
                for pokestop_type, last_fetch in self.last_fetch_times.items():
                    age = current_time - last_fetch
                    if age > timedelta(minutes=max_age_minutes):
                        expired_types.append(pokestop_type)
                
                for pokestop_type in expired_types:
                    if pokestop_type in self.cache_memory:
                        del self.cache_memory[pokestop_type]
                    if pokestop_type in self.last_fetch_times:
                        del self.last_fetch_times[pokestop_type]
                    if pokestop_type in self.cache_locks:
                        del self.cache_locks[pokestop_type]
                    cleaned += 1
                    logger.debug(f"Cleaned up old in-memory cache for {pokestop_type}")
                    
        except Exception as e:
            logger.error(f"Error during memory cache cleanup: {e}")
        
        return cleaned
    
    def get_memory_usage_stats(self) -> Dict:
        """Get memory usage statistics for monitoring."""
        import sys
        
        stats = {
            'cached_types': len(self.cache_memory),
            'active_locks': len(self.cache_locks),
            'cache_dir': self.cache_dir,
            'file_storage_available': self.cache_dir is not None
        }
        
        # Calculate approximate memory usage
        total_stops = 0
        for data in self.cache_memory.values():
            total_stops += sum(len(stops) for stops in data.get('stops', {}).values())
        
        stats['total_cached_stops'] = total_stops
        
        # Get memory usage of cache objects
        try:
            cache_size = sys.getsizeof(self.cache_memory)
            for data in self.cache_memory.values():
                cache_size += sys.getsizeof(data)
            stats['approximate_memory_kb'] = cache_size // 1024
        except Exception:
            stats['approximate_memory_kb'] = 'unknown'
        
        return stats

# Create global cache manager instance
cache_manager = RenderCacheManager()

def deduplicate_stops(stops: List[Dict]) -> List[Dict]:
    """Remove duplicate stops based on coordinates and character."""
    seen = set()
    unique_stops = []
    
    for stop in stops:
        try:
            # Create unique identifier
            identifier = (
                round(float(stop['lat']), 6),  # Round to ~1m precision
                round(float(stop['lng']), 6),
                stop['character']
            )
            
            if identifier not in seen:
                seen.add(identifier)
                unique_stops.append(stop)
            else:
                logger.debug(f"Filtered duplicate stop at {stop['lat']}, {stop['lng']}")
                
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Invalid stop data: {stop}, error: {e}")
            continue
    
    return unique_stops