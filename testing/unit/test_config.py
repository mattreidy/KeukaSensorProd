#!/usr/bin/env python3
"""
Unit tests for configuration management
"""

import pytest
import os
import tempfile
from pathlib import Path
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

class TestConfiguration:
    """Test configuration loading and validation"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_env = {
            "KEUKA_TEST_MODE": "1",
            "KEUKA_MOCK_HARDWARE": "1"
        }
        for key, value in self.test_env.items():
            os.environ[key] = value
    
    def teardown_method(self):
        """Clean up test environment"""
        for key in self.test_env:
            if key in os.environ:
                del os.environ[key]
    
    def test_config_module_import(self):
        """Test that core config module can be imported"""
        from keuka.core import config
        assert config is not None
    
    def test_config_constants_exist(self):
        """Test that required configuration constants exist"""
        from keuka.core import config
        
        # Check for essential config attributes
        required_attrs = ['VERSION', 'APP_DIR']
        for attr in required_attrs:
            assert hasattr(config, attr), f"Missing config attribute: {attr}"
    
    def test_config_paths_are_paths(self):
        """Test that path configurations are Path objects"""
        from keuka.core import config
        
        if hasattr(config, 'APP_DIR'):
            assert isinstance(config.APP_DIR, Path), "APP_DIR should be a Path object"
    
    @pytest.mark.parametrize("config_file", [
        "sensors.conf.template",
        "camera.conf.template"
    ])
    def test_config_templates_exist(self, config_file):
        """Test that configuration templates exist"""
        template_path = PROJECT_ROOT / "configuration" / "templates" / config_file
        assert template_path.exists(), f"Configuration template not found: {config_file}"
    
    def test_environment_templates_exist(self):
        """Test that environment templates exist"""
        env_dir = PROJECT_ROOT / "deployment" / "environment"
        
        templates = [
            "keuka-sensor.env.template",
            "keuka.env.template"
        ]
        
        for template in templates:
            template_path = env_dir / template
            assert template_path.exists(), f"Environment template not found: {template}"
    
    def test_config_template_content(self):
        """Test that config templates have expected content"""
        sensor_template = PROJECT_ROOT / "configuration" / "templates" / "sensors.conf.template"
        
        if sensor_template.exists():
            content = sensor_template.read_text()
            # Should contain placeholder for GPIO pins
            assert "TRIG_PIN" in content or "ECHO_PIN" in content or "pin" in content.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])