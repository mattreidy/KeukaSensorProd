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
                                        # Handle request in a separate thread to avoid blocking SSE stream
                                        request_thread = threading.Thread(
                                            target=self._handle_http_request,
                                            args=(data,),
                                            daemon=True,
                                            name=f"tunnel-req-{data.get('requestId', 'unknown')}"
                                        )
                                        request_thread.start()
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
        request_id = request_data.get('requestId', 'unknown')
        
        try:
            method = request_data['method']
            path = request_data['path']
            headers = request_data.get('headers', {})
            query = request_data.get('query', {})
            body = request_data.get('body')
            
            logger.info(f"Processing {method} {path} (ID: {request_id})")
            
            # Forward request to local Flask server
            local_url = urljoin(self.local_url, path)
            
            # Prepare request parameters with more robust timeout
            request_kwargs = {
                'method': method,
                'url': local_url,
                'params': query,
                'timeout': (10, 20),  # (connect_timeout, read_timeout) - more conservative
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
            
            # Make request to local Flask server with better error handling
            try:
                # Check if this looks like an SSE request
                is_sse_request = (
                    path.endswith('.sse') or 
                    path.endswith('/sse') or
                    forward_headers.get('Accept', '').find('text/event-stream') != -1
                )
                
                if is_sse_request:
                    # Handle SSE streaming requests specially
                    logger.info(f"Handling SSE stream for {method} {path} (ID: {request_id})")
                    self._handle_sse_request(request_id, request_kwargs)
                else:
                    # Handle normal requests
                    response = requests.request(**request_kwargs)
                    logger.debug(f"Local request successful: {response.status_code} (ID: {request_id})")
                    
                    # Send response back through tunnel
                    self._send_response(request_id, response)
                
            except requests.Timeout as e:
                logger.warning(f"Local request timeout for {method} {path} (ID: {request_id}): {e}")
                self._send_error_response(request_id, "Request timeout - the sensor is busy", status_code=503)
                
            except requests.ConnectionError as e:
                logger.error(f"Local connection error for {method} {path} (ID: {request_id}): {e}")
                self._send_error_response(request_id, "Service temporarily unavailable", status_code=503)
                
            except requests.RequestException as e:
                logger.error(f"Local request error for {method} {path} (ID: {request_id}): {e}")
                self._send_error_response(request_id, "Request processing failed", status_code=502)
            
        except KeyError as e:
            logger.error(f"Invalid request data format (ID: {request_id}): missing {e}")
            self._send_error_response(request_id, "Invalid request format", status_code=400)
            
        except Exception as e:
            logger.error(f"Unexpected error handling HTTP request (ID: {request_id}): {e}")
            self._send_error_response(request_id, "Internal tunnel error", status_code=500)

    def _handle_sse_request(self, request_id, request_kwargs):
        """Handle Server-Sent Events streaming requests"""
        try:
            # For SSE through tunnel, collect a few events and send as complete response
            # This prevents infinite streaming while providing multiple updates
            request_kwargs['stream'] = True
            request_kwargs['timeout'] = (10, 60)  # 60 second read timeout to collect events
            
            logger.info(f"Collecting SSE events for {request_id}")
            
            response = requests.request(**request_kwargs)
            
            if response.status_code != 200:
                logger.warning(f"SSE request failed with status {response.status_code} (ID: {request_id})")
                self._send_error_response(request_id, f"SSE endpoint returned {response.status_code}", response.status_code)
                return
            
            # Collect multiple SSE events for a better user experience
            sse_content = ""
            line_buffer = ""
            events_collected = 0
            max_events = 3  # Collect up to 3 events
            start_time = time.time()
            
            try:
                # Read multiple SSE events
                for chunk in response.iter_content(chunk_size=512, decode_unicode=True):
                    if not chunk:
                        continue
                        
                    line_buffer += chunk
                    sse_content += chunk
                    
                    # Check if we have a complete SSE event (ends with \n\n)
                    if '\n\n' in line_buffer:
                        events_collected += 1
                        line_buffer = ""  # Reset for next event
                        
                        # Stop after collecting enough events or timeout
                        if events_collected >= max_events or (time.time() - start_time) > 30:
                            break
                    
                    # Safety limit - don't let SSE content get too large
                    if len(sse_content) > 100000:  # 100KB limit
                        logger.warning(f"SSE content too large for request {request_id}, stopping collection")
                        break
                
                # Close the response stream
                response.close()
                
                if sse_content.strip():
                    # Send the collected SSE content as a complete response
                    response_headers = {
                        'Content-Type': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive',  # Keep connection alive so browser doesn't treat as error
                    }
                    
                    # Add a final event to signal end of this batch
                    sse_content += "event: batch_end\ndata: {\"batch_complete\": true}\n\n"
                    
                    tunnel_response = requests.post(
                        self.response_url,
                        data=sse_content.encode('utf-8'),
                        headers={
                            'X-Request-ID': request_id,
                            'X-Response-Status': '200',
                            'X-Response-Headers': json.dumps(response_headers),
                            'Content-Type': 'application/octet-stream'
                        },
                        timeout=15
                    )
                    tunnel_response.raise_for_status()
                    
                    logger.info(f"SSE batch sent for request {request_id} ({events_collected} events, {len(sse_content)} bytes)")
                else:
                    logger.warning(f"No SSE content received for request {request_id}")
                    self._send_error_response(request_id, "No SSE data received", status_code=204)
                    
            except requests.RequestException as e:
                logger.error(f"SSE stream error for request {request_id}: {e}")
                self._send_error_response(request_id, "SSE connection failed", status_code=502)
                
        except Exception as e:
            logger.error(f"Error handling SSE request {request_id}: {e}")
            self._send_error_response(request_id, "SSE streaming failed", status_code=500)

    def _send_response(self, request_id, response):
        """Send HTTP response back through the tunnel"""
        try:
            # Prepare response headers
            response_headers = {}
            for key, value in response.headers.items():
                # Skip headers that might cause issues
                if key.lower() not in ['connection', 'transfer-encoding', 'content-encoding']:
                    response_headers[key] = value
            
            # Limit response size to prevent tunnel overload
            content = response.content
            max_content_size = 10 * 1024 * 1024  # 10MB limit
            if len(content) > max_content_size:
                logger.warning(f"Response too large ({len(content)} bytes), truncating (ID: {request_id})")
                content = content[:max_content_size]
                response_headers['X-Truncated'] = 'true'
            
            # Send response back to keuka.org with timeout
            tunnel_response = requests.post(
                self.response_url,
                data=content,
                headers={
                    'X-Request-ID': request_id,
                    'X-Response-Status': str(response.status_code),
                    'X-Response-Headers': json.dumps(response_headers),
                    'Content-Type': 'application/octet-stream'
                },
                timeout=25  # Reduced timeout for better reliability
            )
            tunnel_response.raise_for_status()
            
            logger.info(f"Response sent for request {request_id}: {response.status_code} ({len(content)} bytes)")
            
        except requests.Timeout:
            logger.error(f"Timeout sending response for request {request_id}")
            # Don't retry - the server-side timeout has probably occurred
            
        except requests.RequestException as e:
            logger.error(f"Network error sending response for request {request_id}: {e}")
            
        except Exception as e:
            logger.error(f"Unexpected error sending response for request {request_id}: {e}")

    def _send_error_response(self, request_id, error_message, status_code=500):
        """Send error response back through the tunnel"""
        try:
            error_html = f"""
            <html>
                <body>
                    <h1>Sensor Error ({status_code})</h1>
                    <p>The sensor encountered an error processing your request:</p>
                    <p><strong>{error_message}</strong></p>
                    <p><small>Request ID: {request_id}</small></p>
                </body>
            </html>
            """
            
            tunnel_response = requests.post(
                self.response_url,
                data=error_html.encode('utf-8'),
                headers={
                    'X-Request-ID': request_id,
                    'X-Response-Status': str(status_code),
                    'X-Response-Headers': json.dumps({'Content-Type': 'text/html'}),
                    'Content-Type': 'application/octet-stream'
                },
                timeout=15  # Shorter timeout for error responses
            )
            tunnel_response.raise_for_status()
            logger.debug(f"Error response sent for request {request_id}: {status_code}")
            
        except requests.Timeout:
            logger.error(f"Timeout sending error response for request {request_id}")
        except requests.RequestException as e:
            logger.error(f"Network error sending error response for request {request_id}: {e}")
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