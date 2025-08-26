#!/usr/bin/env python3
"""
Integration tests for HTTP endpoints
"""

import pytest
import os
import sys
from pathlib import Path
import json

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

class TestHTTPEndpoints:
    """Test HTTP endpoints using Flask test client"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_env = {
            "KEUKA_TEST_MODE": "1",
            "KEUKA_MOCK_HARDWARE": "1",
            "KEUKA_SAFE_MODE": "1"
        }
        for key, value in self.test_env.items():
            os.environ[key] = value
            
        # Create Flask test client
        from keuka.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'app_context'):
            self.app_context.pop()
            
        for key in self.test_env:
            if key in os.environ:
                del os.environ[key]
    
    def test_root_endpoint(self):
        """Test root endpoint returns sensor data format"""
        response = self.client.get('/')
        
        assert response.status_code == 200
        assert response.content_type.startswith('text/plain')
        
        # Should return comma-separated values (temp,distance,lat,lon,elevation,fqdn)
        data = response.get_data(as_text=True)
        assert ',' in data, "Root endpoint should return comma-separated values"
        
        # Should be parseable as five floats plus a string
        parts = data.strip().split(',')
        assert len(parts) == 6, "Root endpoint should return exactly six values (temp,distance,lat,lon,elevation,fqdn)"
        
        try:
            temp, distance, lat, lon, elevation = map(float, parts[:5])
            fqdn = parts[5]
            assert isinstance(temp, float)
            assert isinstance(distance, float)
            assert isinstance(lat, float)
            assert isinstance(lon, float)
            assert isinstance(elevation, float)
            assert isinstance(fqdn, str) and len(fqdn) > 0
        except (ValueError, IndexError):
            pytest.fail("Root endpoint should return five floats and a non-empty string")
    
    def test_health_json_endpoint(self):
        """Test health JSON endpoint"""
        response = self.client.get('/health.json')
        
        # Should return JSON (might be 200 or error depending on setup)
        assert response.content_type.startswith('application/json')
        
        if response.status_code == 200:
            data = response.get_json()
            assert isinstance(data, dict)
            
            # Should have expected fields
            expected_fields = ['time_utc', 'tempF', 'distanceInches', 'camera', 'gps']
            for field in expected_fields:
                assert field in data, f"Missing field in health JSON: {field}"
            
            # GPS sub-object should have lat, lon, elevation_ft
            if 'gps' in data and data['gps']:
                gps_fields = ['lat', 'lon', 'elevation_ft']
                for field in gps_fields:
                    assert field in data['gps'], f"Missing GPS field: {field}"
    
    def test_health_page_endpoint(self):
        """Test health dashboard page"""
        response = self.client.get('/health')
        
        # Should return HTML page
        assert response.status_code == 200
        assert response.content_type.startswith('text/html')
        
        html_content = response.get_data(as_text=True)
        assert '<html>' in html_content or '<HTML>' in html_content
        assert 'Health' in html_content
    
    def test_webcam_page_endpoint(self):
        """Test webcam page"""
        response = self.client.get('/webcam')
        
        assert response.status_code == 200
        assert response.content_type.startswith('text/html')
        
        html_content = response.get_data(as_text=True)
        assert 'Webcam' in html_content
    
    def test_snapshot_endpoint(self):
        """Test snapshot endpoint"""
        response = self.client.get('/snapshot')
        
        # In test mode, might return 503 (no camera) or actual image
        assert response.status_code in [200, 503]
        
        if response.status_code == 200:
            assert response.content_type.startswith('image/')
    
    def test_stream_endpoint(self):
        """Test MJPEG stream endpoint"""
        response = self.client.get('/stream')
        
        # In test mode, might return 503 (no camera) or stream
        assert response.status_code in [200, 503]
        
        if response.status_code == 200:
            assert 'multipart' in response.content_type
    
    def test_admin_endpoints_require_auth(self):
        """Test that admin endpoints require authentication"""
        admin_endpoints = [
            '/admin',
            '/admin/wifi',
            '/admin/update'
        ]
        
        for endpoint in admin_endpoints:
            response = self.client.get(endpoint)
            # Should require authentication (401) or redirect (302/3xx)
            assert response.status_code in [401, 302, 303, 307, 308], \
                f"Admin endpoint {endpoint} should require authentication"
    
    def test_api_endpoints_json_response(self):
        """Test API endpoints return JSON"""
        api_endpoints = [
            '/api/wifi/status',
            '/api/wanip'
        ]
        
        for endpoint in api_endpoints:
            response = self.client.get(endpoint)
            
            # Should return JSON (might require auth)
            if response.status_code == 200:
                assert response.content_type.startswith('application/json')
                data = response.get_json()
                assert isinstance(data, dict)
            elif response.status_code == 401:
                # Authentication required - acceptable
                assert response.content_type.startswith('application/json')
            else:
                # Other status codes are acceptable in test mode
                pass
    
    def test_nonexistent_endpoint(self):
        """Test that nonexistent endpoints return 404"""
        response = self.client.get('/nonexistent-endpoint-12345')
        assert response.status_code == 404
    
    def test_health_sse_endpoint(self):
        """Test Server-Sent Events endpoint"""
        response = self.client.get('/health.sse')
        
        # Should return event stream or error
        assert response.status_code in [200, 500, 503]
        
        if response.status_code == 200:
            assert 'text/event-stream' in response.content_type


class TestHTTPEndpointSafety:
    """Test that endpoints are safe to test and don't modify system"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_env = {
            "KEUKA_TEST_MODE": "1",
            "KEUKA_MOCK_HARDWARE": "1",
            "KEUKA_SAFE_MODE": "1"
        }
        for key, value in self.test_env.items():
            os.environ[key] = value
            
        from keuka.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'app_context'):
            self.app_context.pop()
            
        for key in self.test_env:
            if key in os.environ:
                del os.environ[key]
    
    def test_no_system_modification_get_requests(self):
        """Test that GET requests don't modify system state"""
        safe_get_endpoints = [
            '/',
            '/health',
            '/health.json',
            '/webcam',
            '/api/wifi/status',
            '/api/wanip'
        ]
        
        for endpoint in safe_get_endpoints:
            # Multiple requests should be consistent
            response1 = self.client.get(endpoint)
            response2 = self.client.get(endpoint)
            
            # Status codes should be consistent
            assert response1.status_code == response2.status_code, \
                f"Inconsistent responses for {endpoint}"
    
    def test_dangerous_endpoints_blocked_in_test_mode(self):
        """Test that dangerous operations are blocked in test mode"""
        # These should be blocked or return safe responses in test mode
        dangerous_posts = [
            '/api/wifi/connect',  # Could change network config
            '/admin/start_update'  # Could update code
        ]
        
        for endpoint in dangerous_posts:
            # Without auth, should get 401 or safe error
            response = self.client.post(endpoint)
            assert response.status_code in [401, 403, 404, 405, 400], \
                f"Dangerous endpoint {endpoint} should be protected"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])