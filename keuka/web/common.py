# web/common.py
# Common utilities and patterns for web routes

from __future__ import annotations
from typing import Dict, Any, Optional, Tuple, Union
from flask import jsonify, request, Response
import logging
from functools import wraps
import traceback

logger = logging.getLogger(__name__)

# Common error response structure
class ApiError(Exception):
    """Standard API error with status code and message."""
    
    def __init__(self, message: str, status_code: int = 400, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)

def standardize_error_response(error: Union[Exception, ApiError, str], status_code: int = 500) -> Tuple[Dict[str, Any], int]:
    """Standardized error response format."""
    if isinstance(error, ApiError):
        return {
            "ok": False,
            "error": error.message,
            "details": error.details
        }, error.status_code
    
    error_msg = str(error) if not isinstance(error, str) else error
    return {
        "ok": False,
        "error": error_msg
    }, status_code

def api_route(f):
    """Decorator for API routes with standardized error handling."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            result = f(*args, **kwargs)
            if isinstance(result, tuple) and len(result) == 2:
                return result  # Already has status code
            return result
        except ApiError as e:
            logger.error(f"API error in {f.__name__}: {e.message}")
            return standardize_error_response(e)
        except Exception as e:
            logger.error(f"Unexpected error in {f.__name__}: {e}\n{traceback.format_exc()}")
            return standardize_error_response("Internal server error", 500)
    return wrapper

def validate_json_request(required_fields: Optional[list] = None) -> Dict[str, Any]:
    """Validate JSON request and return parsed data."""
    if not request.is_json:
        raise ApiError("Request must be JSON", 400)
    
    try:
        data = request.get_json(force=True, silent=False)
        if data is None:
            raise ApiError("Invalid JSON data", 400)
    except Exception as e:
        raise ApiError(f"JSON parsing error: {str(e)}", 400)
    
    if required_fields:
        missing = [field for field in required_fields if field not in data]
        if missing:
            raise ApiError(f"Missing required fields: {', '.join(missing)}", 400)
    
    return data

def safe_float_conversion(value: Any, default: float = 0.0) -> float:
    """Safely convert NaN values to default for legacy compatibility."""
    try:
        if value != value:  # NaN check
            return default
        return float(value)
    except (ValueError, TypeError):
        return default

def get_client_info() -> Dict[str, str]:
    """Get client information for logging/monitoring."""
    return {
        "ip": request.remote_addr or "unknown",
        "user_agent": request.headers.get("User-Agent", "unknown"),
        "method": request.method,
        "path": request.path,
        "referrer": request.headers.get("Referer", ""),
    }

def log_request(level: str = "info") -> None:
    """Log request details."""
    client_info = get_client_info()
    log_func = getattr(logger, level.lower(), logger.info)
    log_func(f"{client_info['method']} {client_info['path']} - {client_info['ip']} - {client_info['user_agent']}")