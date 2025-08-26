# coordinate_parser.py
# Utility functions for parsing and normalizing GPS coordinates from various formats

import re
from typing import Tuple, Optional, Union


def normalize_coordinate(coord_value: Union[str, float, int], coord_type: str = "lat") -> Optional[float]:
    """
    Normalize a coordinate value to signed decimal degrees.
    
    Args:
        coord_value: Coordinate value in various formats:
                    - Numeric: 42.606, -77.091
                    - String with direction: "37.677715 N", "77.612540 W"  
                    - NMEA format: "4206.0600,N", "07709.1000,W"
        coord_type: "lat" for latitude or "lon" for longitude
        
    Returns:
        Normalized coordinate as signed decimal degrees, or None if invalid
    """
    if coord_value is None or coord_value == "":
        return None
        
    # Handle numeric input
    if isinstance(coord_value, (int, float)):
        return float(coord_value)
    
    # Handle string input
    coord_str = str(coord_value).strip()
    if not coord_str:
        return None
    
    # Try parsing as plain decimal number first
    try:
        return float(coord_str)
    except ValueError:
        pass
    
    # Parse string with direction suffix (e.g., "37.677715 N", "77.612540 W")
    direction_pattern = r'^([+-]?[\d.]+)\s*([NSEW])$'
    match = re.match(direction_pattern, coord_str.upper())
    if match:
        value_str, direction = match.groups()
        try:
            value = float(value_str)
            # Apply sign based on direction
            if direction in ['S', 'W']:
                value = -abs(value)  # Force negative for South/West
            else:  # N, E
                value = abs(value)   # Force positive for North/East
            return value
        except ValueError:
            pass
    
    # Parse NMEA format (e.g., "4206.0600,N", "07709.1000,W")
    nmea_pattern = r'^(\d{2,3})(\d{2}\.\d+),([NSEW])$'
    match = re.match(nmea_pattern, coord_str.upper())
    if match:
        degrees_str, minutes_str, direction = match.groups()
        try:
            degrees = int(degrees_str)
            minutes = float(minutes_str)
            value = degrees + minutes / 60.0
            # Apply sign based on direction
            if direction in ['S', 'W']:
                value = -value
            return value
        except ValueError:
            pass
    
    return None


def normalize_gps_coordinates(latitude: Union[str, float, int, None], 
                            longitude: Union[str, float, int, None]) -> Tuple[Optional[float], Optional[float]]:
    """
    Normalize a pair of GPS coordinates to signed decimal degrees.
    
    Args:
        latitude: Latitude in various formats
        longitude: Longitude in various formats
        
    Returns:
        Tuple of (normalized_lat, normalized_lon) or (None, None) if invalid
    """
    norm_lat = normalize_coordinate(latitude, "lat")
    norm_lon = normalize_coordinate(longitude, "lon")
    
    # Validate ranges
    if norm_lat is not None and not (-90 <= norm_lat <= 90):
        norm_lat = None
    if norm_lon is not None and not (-180 <= norm_lon <= 180):
        norm_lon = None
        
    return norm_lat, norm_lon


def is_valid_coordinate_pair(latitude: Optional[float], longitude: Optional[float]) -> bool:
    """
    Check if a coordinate pair is valid.
    
    Args:
        latitude: Latitude in decimal degrees
        longitude: Longitude in decimal degrees
        
    Returns:
        True if both coordinates are valid numbers within expected ranges
    """
    if latitude is None or longitude is None:
        return False
    
    try:
        lat_val = float(latitude)
        lon_val = float(longitude)
        
        # Check for NaN
        if lat_val != lat_val or lon_val != lon_val:
            return False
            
        # Check ranges
        return -90 <= lat_val <= 90 and -180 <= lon_val <= 180
    except (ValueError, TypeError):
        return False