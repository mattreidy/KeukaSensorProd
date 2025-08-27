#!/usr/bin/env python3
"""
HTTP Tunnel Client for Pi Sensors
Establishes SSE connection to keuka.org for web interface proxying
"""

import json
import logging
import threading
import time
import requests
from urllib.parse import urljoin
import os
import sys

# Add the keuka directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Read from environment variables first, then config as fallback
SENSOR_NAME = os.environ.get('SENSOR_NAME')
KEUKA_SERVER_URL = os.environ.get('KEUKA_SERVER_URL', 'https://keuka.org')

# If not in environment, try importing from config
if not SENSOR_NAME:
    try:
        from config import SENSOR_NAME, KEUKA_SERVER_URL
    except ImportError:
        pass

# Final fallback
if not SENSOR_NAME:
    SENSOR_NAME = "keukasensor1"

logger = logging.getLogger(__name__)

class TunnelClient:
    def __init__(self, sensor_name=None, server_url=None, local_port=5000):
        self.sensor_name = sensor_name or SENSOR_NAME
        self.server_url = server_url or KEUKA_SERVER_URL
        self.local_port = local_port
        self.local_url = f"http://localhost:{local_port}"
        
        self.tunnel_url = f"{self.server_url}/api/sensors/{self.sensor_name}/tunnel"
        self.response_url = f"{self.server_url}/api/sensors/{self.sensor_name}/tunnel/response"
        
        self.running = False
        self.thread = None
        
        logger.info(f"TunnelClient initialized for {self.sensor_name}")
        logger.info(f"Tunnel URL: {self.tunnel_url}")
        logger.info(f"Local Flask server: {self.local_url}")

    def start(self):
        """Start the tunnel client in a background thread"""
        if self.running:
            logger.warning("Tunnel client already running")
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._run_tunnel, daemon=True)
        self.thread.start()
        logger.info("Tunnel client started")

    def stop(self):
        """Stop the tunnel client"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("Tunnel client stopped")

    def _run_tunnel(self):
        """Main tunnel loop - handles SSE connection and request processing"""
        retry_delay = 1
        max_retry_delay = 60
        
        while self.running:
            try:
                logger.info(f"Connecting to tunnel endpoint: {self.tunnel_url}")
                
                # Connect to SSE endpoint with robust configuration
                headers = {
                    'Cache-Control': 'no-cache',
                    'Accept': 'text/event-stream',
                    'Connection': 'keep-alive',
                    'User-Agent': 'KeukaSensorTunnel/1.0'
                }
                
                # Use longer timeout and disable verify for local testing if needed
                session = requests.Session()
                session.headers.update(headers)
                response = session.get(
                    self.tunnel_url, 
                    stream=True, 
                    timeout=(30, None),  # 30s connect, infinite read
                    headers=headers
                )
                response.raise_for_status()
                
                retry_delay = 1  # Reset retry delay on successful connection
                
                # Process SSE messages with improved chunked handling
                last_heartbeat = time.time()
                
                try:
                    for line in response.iter_lines(decode_unicode=True, chunk_size=1):
                        if not self.running:
                            break
                        
                        # Update heartbeat on any activity (including None/empty lines)
                        current_time = time.time()
                        last_heartbeat = current_time
                        
                        # Check for connection staleness
                        if current_time - last_heartbeat > 120:  # Extended to 2 minutes
                            logger.warning("No SSE activity for 120+ seconds, reconnecting...")
                            break
                        
                        if line is None:
                            continue
                            
                        line = line.strip()
                        
                        # SSE format: "data: {json}"
                        if line.startswith('data: '):
                            data_str = line[6:]  # Remove "data: " prefix
                            if data_str.strip():
                                try:
                                    data = json.loads(data_str)
                                    if data.get('type') == 'connected':
                                        logger.info(f"Tunnel established for {data.get('sensorName')}")
                                    elif data.get('type') == 'http_request':
                                        self._handle_http_request(data)
                                    elif data.get('type') == 'ping':
                                        logger.debug("Received server ping")
                                except json.JSONDecodeError as e:
                                    logger.error(f"Failed to parse SSE data: {e}")
                                except Exception as e:
                                    logger.error(f"Error processing SSE message: {e}")
                        elif line == "":
                            # Empty line - message boundary
                            logger.debug("SSE message boundary")
                        elif line.startswith(':'):
                            # Comment line
                            logger.debug(f"SSE comment: {line}")
                        else:
                            # Other SSE fields (event, id, retry)
                            logger.debug(f"SSE field: {line}")
                except Exception as chunk_error:
                    logger.error(f"Chunked encoding error: {chunk_error}")
                    break
                            
            except requests.RequestException as e:
                if self.running:
                    logger.error(f"Tunnel connection failed: {e}")
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
            except Exception as e:
                if self.running:
                    logger.error(f"Unexpected tunnel error: {e}")
                    time.sleep(retry_delay)

    def _handle_http_request(self, request_data):
        """Process an HTTP request from the tunnel and send response back"""
        try:
            request_id = request_data['requestId']
            method = request_data['method']
            path = request_data['path']
            headers = request_data.get('headers', {})
            query = request_data.get('query', {})
            body = request_data.get('body')
            
            logger.info(f"Processing {method} {path} (ID: {request_id})")
            
            # Forward request to local Flask server
            local_url = urljoin(self.local_url, path)
            
            # Prepare request parameters
            request_kwargs = {
                'method': method,
                'url': local_url,
                'params': query,
                'timeout': 25,  # Leave buffer for 30s tunnel timeout
            }
            
            # Handle request body
            if body and method.upper() in ['POST', 'PUT', 'PATCH']:
                if isinstance(body, dict):
                    request_kwargs['json'] = body
                else:
                    request_kwargs['data'] = body
            
            # Forward relevant headers (exclude keuka.org specific headers)
            forward_headers = {}
            skip_headers = {
                'host', 'x-forwarded-for', 'x-real-ip', 'x-request-id', 
                'x-response-status', 'x-response-headers', 'connection',
                'cache-control', 'accept-encoding'
            }
            
            for key, value in headers.items():
                if key.lower() not in skip_headers:
                    forward_headers[key] = value
                    
            if forward_headers:
                request_kwargs['headers'] = forward_headers
            
            # Make request to local Flask server
            response = requests.request(**request_kwargs)
            
            # Send response back through tunnel
            self._send_response(request_id, response)
            
        except Exception as e:
            logger.error(f"Error handling HTTP request {request_data.get('requestId', 'unknown')}: {e}")
            # Send error response
            try:
                self._send_error_response(request_data.get('requestId'), str(e))
            except:
                pass

    def _send_response(self, request_id, response):
        """Send HTTP response back through the tunnel"""
        try:
            # Prepare response headers
            response_headers = {}
            for key, value in response.headers.items():
                # Skip headers that might cause issues
                if key.lower() not in ['connection', 'transfer-encoding', 'content-encoding']:
                    response_headers[key] = value
            
            # Send response back to keuka.org
            tunnel_response = requests.post(
                self.response_url,
                data=response.content,
                headers={
                    'X-Request-ID': request_id,
                    'X-Response-Status': str(response.status_code),
                    'X-Response-Headers': json.dumps(response_headers),
                    'Content-Type': 'application/octet-stream'
                },
                timeout=30
            )
            tunnel_response.raise_for_status()
            
            logger.info(f"Response sent for request {request_id}: {response.status_code}")
            
        except Exception as e:
            logger.error(f"Failed to send response for request {request_id}: {e}")

    def _send_error_response(self, request_id, error_message):
        """Send error response back through the tunnel"""
        try:
            error_html = f"""
            <html>
                <body>
                    <h1>Sensor Error</h1>
                    <p>The sensor encountered an error processing your request:</p>
                    <p><strong>{error_message}</strong></p>
                </body>
            </html>
            """
            
            tunnel_response = requests.post(
                self.response_url,
                data=error_html.encode('utf-8'),
                headers={
                    'X-Request-ID': request_id,
                    'X-Response-Status': '500',
                    'X-Response-Headers': json.dumps({'Content-Type': 'text/html'}),
                    'Content-Type': 'application/octet-stream'
                },
                timeout=30
            )
            tunnel_response.raise_for_status()
            
        except Exception as e:
            logger.error(f"Failed to send error response for request {request_id}: {e}")


# Global tunnel client instance
_tunnel_client = None

def start_tunnel():
    """Start the tunnel client (call this from your main app)"""
    global _tunnel_client
    if _tunnel_client is None:
        _tunnel_client = TunnelClient()
        _tunnel_client.start()
        return True
    return False

def stop_tunnel():
    """Stop the tunnel client"""
    global _tunnel_client
    if _tunnel_client:
        _tunnel_client.stop()
        _tunnel_client = None

def is_tunnel_running():
    """Check if tunnel is running"""
    return _tunnel_client is not None and _tunnel_client.running

if __name__ == "__main__":
    # For testing - run standalone
    logging.basicConfig(level=logging.INFO)
    client = TunnelClient()
    try:
        client.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping tunnel client...")
        client.stop()