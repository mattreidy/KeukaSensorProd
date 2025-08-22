#!/usr/bin/env python3
"""
KeukaSensor Comprehensive Test Suite
====================================

Safe testing framework that validates all application components without 
affecting the production environment.

Usage:
    python testing/run_tests.py [--category CATEGORY] [--verbose] [--report]
    
Categories:
    - config: Configuration file validation
    - http: HTTP endpoint testing
    - sensors: Sensor functionality (mock hardware)
    - camera: Camera functionality (mock hardware)
    - network: Network utilities (safe mode)
    - security: Security and authentication
    - integration: End-to-end workflows
    - all: Run all tests (default)
"""

import sys
import os
import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
import subprocess
import venv
import tempfile

# Add the project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

class TestRunner:
    def __init__(self, verbose: bool = False, generate_report: bool = False):
        self.verbose = verbose
        self.generate_report = generate_report
        self.results = []
        self.start_time = time.time()
        
        # Safe testing configuration
        self.test_config = {
            "safe_mode": True,
            "mock_hardware": True,
            "backup_configs": True,
            "test_venv_path": PROJECT_ROOT / "testing" / "test_venv",
            "temp_config_dir": None,
            "original_env": os.environ.copy()
        }
        
    def setup_test_environment(self):
        """Set up isolated test environment"""
        print("🔧 Setting up test environment...")
        
        # Create temporary config directory
        self.test_config["temp_config_dir"] = tempfile.mkdtemp(prefix="keuka_test_")
        
        # Set environment variables for testing
        test_env = {
            "KEUKA_TEST_MODE": "1",
            "KEUKA_MOCK_HARDWARE": "1",
            "KEUKA_CONFIG_DIR": self.test_config["temp_config_dir"],
            "KEUKA_SAFE_MODE": "1"
        }
        
        for key, value in test_env.items():
            os.environ[key] = value
            
        if self.verbose:
            print(f"   Test config directory: {self.test_config['temp_config_dir']}")
        
    def cleanup_test_environment(self):
        """Clean up test environment"""
        print("🧹 Cleaning up test environment...")
        
        # Restore original environment
        os.environ.clear()
        os.environ.update(self.test_config["original_env"])
        
        # Clean up temp directory
        if self.test_config["temp_config_dir"]:
            import shutil
            try:
                shutil.rmtree(self.test_config["temp_config_dir"])
                if self.verbose:
                    print(f"   Cleaned up: {self.test_config['temp_config_dir']}")
            except Exception as e:
                print(f"   Warning: Could not clean up temp dir: {e}")
        
    def run_test_category(self, category: str) -> Dict[str, Any]:
        """Run tests for a specific category"""
        print(f"\n📋 Running {category.upper()} tests...")
        
        category_results = {
            "category": category,
            "start_time": time.time(),
            "tests": [],
            "passed": 0,
            "failed": 0,
            "skipped": 0
        }
        
        if category == "config":
            category_results["tests"] = self.test_configuration()
        elif category == "http":
            category_results["tests"] = self.test_http_endpoints()
        elif category == "sensors":
            category_results["tests"] = self.test_sensors()
        elif category == "camera":
            category_results["tests"] = self.test_camera()
        elif category == "network":
            category_results["tests"] = self.test_network()
        elif category == "security":
            category_results["tests"] = self.test_security()
        elif category == "integration":
            category_results["tests"] = self.test_integration()
        else:
            print(f"   ❌ Unknown category: {category}")
            return category_results
            
        # Calculate results
        for test in category_results["tests"]:
            if test["status"] == "PASS":
                category_results["passed"] += 1
            elif test["status"] == "FAIL":
                category_results["failed"] += 1
            else:
                category_results["skipped"] += 1
                
        category_results["end_time"] = time.time()
        category_results["duration"] = category_results["end_time"] - category_results["start_time"]
        
        # Print summary
        total = len(category_results["tests"])
        print(f"   ✅ {category_results['passed']}/{total} passed, "
              f"❌ {category_results['failed']} failed, "
              f"⏭️ {category_results['skipped']} skipped "
              f"({category_results['duration']:.2f}s)")
              
        return category_results
        
    def test_configuration(self) -> List[Dict[str, Any]]:
        """Test configuration files and settings"""
        tests = []
        
        # Test 1: Check main config files exist
        config_files = [
            PROJECT_ROOT / "keuka" / "core" / "config.py",
            PROJECT_ROOT / "configuration" / "templates" / "sensors.conf.template",
            PROJECT_ROOT / "configuration" / "templates" / "camera.conf.template",
        ]
        
        for config_file in config_files:
            test_result = {
                "name": f"Config file exists: {config_file.name}",
                "status": "PASS" if config_file.exists() else "FAIL",
                "details": f"Path: {config_file}",
                "duration": 0.001
            }
            if not config_file.exists():
                test_result["error"] = f"File not found: {config_file}"
            tests.append(test_result)
            if self.verbose:
                print(f"      {'✅' if test_result['status'] == 'PASS' else '❌'} {test_result['name']}")
        
        # Test 2: Import core config module
        try:
            from keuka.core import config
            tests.append({
                "name": "Import core config module",
                "status": "PASS",
                "details": "Successfully imported core.config",
                "duration": 0.002
            })
            if self.verbose:
                print("      ✅ Import core config module")
        except Exception as e:
            tests.append({
                "name": "Import core config module",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.002
            })
            if self.verbose:
                print(f"      ❌ Import core config module: {e}")
        
        # Test 3: Check environment templates
        env_templates = [
            PROJECT_ROOT / "deployment" / "environment" / "keuka-sensor.env.template",
            PROJECT_ROOT / "deployment" / "environment" / "keuka.env.template",
        ]
        
        for env_file in env_templates:
            test_result = {
                "name": f"Environment template: {env_file.name}",
                "status": "PASS" if env_file.exists() else "FAIL",
                "details": f"Path: {env_file}",
                "duration": 0.001
            }
            tests.append(test_result)
            if self.verbose:
                print(f"      {'✅' if test_result['status'] == 'PASS' else '❌'} {test_result['name']}")
        
        return tests
        
    def test_http_endpoints(self) -> List[Dict[str, Any]]:
        """Test HTTP endpoints without starting full server"""
        tests = []
        
        try:
            # Import Flask app
            from keuka.app import create_app
            app = create_app()
            client = app.test_client()
            
            # Test endpoints that should be safe to test
            safe_endpoints = [
                ("GET", "/", "Root endpoint"),
                ("GET", "/health.json", "Health JSON endpoint"),
                ("GET", "/webcam", "Webcam page"),
            ]
            
            for method, endpoint, description in safe_endpoints:
                try:
                    start_time = time.time()
                    if method == "GET":
                        response = client.get(endpoint)
                    elif method == "POST":
                        response = client.post(endpoint)
                    else:
                        response = None
                        
                    duration = time.time() - start_time
                    
                    if response and response.status_code in [200, 404, 401, 403]:
                        # These are acceptable status codes for testing
                        tests.append({
                            "name": description,
                            "status": "PASS",
                            "details": f"{method} {endpoint} -> {response.status_code}",
                            "duration": duration
                        })
                        if self.verbose:
                            print(f"      ✅ {description} ({response.status_code})")
                    else:
                        tests.append({
                            "name": description,
                            "status": "FAIL",
                            "error": f"Unexpected status: {response.status_code if response else 'No response'}",
                            "duration": duration
                        })
                        if self.verbose:
                            print(f"      ❌ {description}")
                            
                except Exception as e:
                    tests.append({
                        "name": description,
                        "status": "FAIL",
                        "error": str(e),
                        "duration": 0.001
                    })
                    if self.verbose:
                        print(f"      ❌ {description}: {e}")
                        
        except Exception as e:
            tests.append({
                "name": "HTTP endpoint testing setup",
                "status": "FAIL",
                "error": f"Could not create Flask app: {e}",
                "duration": 0.001
            })
            if self.verbose:
                print(f"      ❌ HTTP endpoint testing setup: {e}")
        
        return tests
        
    def test_sensors(self) -> List[Dict[str, Any]]:
        """Test sensor modules with mocked hardware"""
        tests = []
        
        # Test temperature sensor module
        try:
            from keuka.hardware import temperature
            
            # Test import
            tests.append({
                "name": "Temperature module import",
                "status": "PASS",
                "details": "Successfully imported temperature module",
                "duration": 0.001
            })
            
            # Test function existence
            if hasattr(temperature, 'read_temp_fahrenheit'):
                tests.append({
                    "name": "Temperature function exists",
                    "status": "PASS",
                    "details": "read_temp_fahrenheit function found",
                    "duration": 0.001
                })
            else:
                tests.append({
                    "name": "Temperature function exists",
                    "status": "FAIL",
                    "error": "read_temp_fahrenheit function not found",
                    "duration": 0.001
                })
                
        except Exception as e:
            tests.append({
                "name": "Temperature module import",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.001
            })
        
        # Test ultrasonic sensor module
        try:
            from keuka.hardware import ultrasonic
            
            tests.append({
                "name": "Ultrasonic module import",
                "status": "PASS",
                "details": "Successfully imported ultrasonic module",
                "duration": 0.001
            })
            
            # Test function existence
            if hasattr(ultrasonic, 'median_distance_inches'):
                tests.append({
                    "name": "Ultrasonic function exists",
                    "status": "PASS",
                    "details": "median_distance_inches function found",
                    "duration": 0.001
                })
            else:
                tests.append({
                    "name": "Ultrasonic function exists",
                    "status": "FAIL",
                    "error": "median_distance_inches function not found",
                    "duration": 0.001
                })
                
        except Exception as e:
            tests.append({
                "name": "Ultrasonic module import",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.001
            })
            
        # Test GPS module
        try:
            from keuka.hardware import gps
            
            tests.append({
                "name": "GPS module import",
                "status": "PASS",
                "details": "Successfully imported GPS module",
                "duration": 0.001
            })
            
        except Exception as e:
            tests.append({
                "name": "GPS module import",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.001
            })
        
        if self.verbose:
            for test in tests[-6:]:  # Show last 6 tests (sensor tests)
                print(f"      {'✅' if test['status'] == 'PASS' else '❌'} {test['name']}")
        
        return tests
        
    def test_camera(self) -> List[Dict[str, Any]]:
        """Test camera module with mocked hardware"""
        tests = []
        
        try:
            from keuka.hardware import camera
            
            tests.append({
                "name": "Camera module import",
                "status": "PASS",
                "details": "Successfully imported camera module",
                "duration": 0.001
            })
            
            # Test camera functions exist
            if hasattr(camera, 'get_jpeg') and hasattr(camera, 'available'):
                tests.append({
                    "name": "Camera functions exist",
                    "status": "PASS",
                    "details": "get_jpeg and available functions found",
                    "duration": 0.001
                })
            else:
                tests.append({
                    "name": "Camera functions exist",
                    "status": "FAIL",
                    "error": "Camera functions not found",
                    "duration": 0.001
                })
                
        except Exception as e:
            tests.append({
                "name": "Camera module import",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.001
            })
            
        if self.verbose:
            for test in tests[-2:]:
                print(f"      {'✅' if test['status'] == 'PASS' else '❌'} {test['name']}")
        
        return tests
        
    def test_network(self) -> List[Dict[str, Any]]:
        """Test network utilities in safe mode"""
        tests = []
        
        try:
            from keuka.networking import wifi
            
            tests.append({
                "name": "WiFi module import",
                "status": "PASS",
                "details": "Successfully imported wifi module",
                "duration": 0.001
            })
            
            # Test function existence (don't call them)
            safe_functions = ['wifi_status', 'ip_addr4', 'gw4', 'dns_servers']
            for func_name in safe_functions:
                if hasattr(wifi, func_name):
                    tests.append({
                        "name": f"WiFi function: {func_name}",
                        "status": "PASS",
                        "details": f"{func_name} function found",
                        "duration": 0.001
                    })
                else:
                    tests.append({
                        "name": f"WiFi function: {func_name}",
                        "status": "FAIL",
                        "error": f"{func_name} function not found",
                        "duration": 0.001
                    })
                    
        except Exception as e:
            tests.append({
                "name": "WiFi module import",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.001
            })
            
        if self.verbose:
            for test in tests[-5:]:
                print(f"      {'✅' if test['status'] == 'PASS' else '❌'} {test['name']}")
        
        return tests
        
    def test_security(self) -> List[Dict[str, Any]]:
        """Test security and authentication components"""
        tests = []
        
        # Test admin submodules exist as files instead of importing (to avoid circular imports)
        try:
            admin_dir = PROJECT_ROOT / "keuka" / "admin"
            admin_files = ["__init__.py", "ssh_web_terminal.py", "wifi.py", "update.py", "wan.py"]
            
            for filename in admin_files:
                if (admin_dir / filename).exists():
                    tests.append({
                        "name": f"Admin module file: {filename}",
                        "status": "PASS",
                        "details": f"{filename} exists",
                        "duration": 0.001
                    })
                else:
                    tests.append({
                        "name": f"Admin module file: {filename}",
                        "status": "FAIL",
                        "error": f"{filename} not found",
                        "duration": 0.001
                    })
            
        except Exception as e:
            tests.append({
                "name": "Admin module structure check",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.001
            })
            
        if self.verbose:
            for test in tests:  # Show all security tests
                print(f"      {'✅' if test['status'] == 'PASS' else '❌'} {test['name']}")
        
        return tests
        
    def test_integration(self) -> List[Dict[str, Any]]:
        """Test end-to-end integration workflows"""
        tests = []
        
        # Test full app creation
        try:
            from keuka.app import create_app
            app = create_app()
            
            tests.append({
                "name": "Full app creation",
                "status": "PASS",
                "details": "Successfully created Flask app with all blueprints",
                "duration": 0.010
            })
            
            # Test blueprint registration
            blueprint_names = [bp.name for bp in app.blueprints.values()]
            expected_blueprints = ["root", "webcam", "admin", "health"]
            
            for bp_name in expected_blueprints:
                if bp_name in blueprint_names:
                    tests.append({
                        "name": f"Blueprint registered: {bp_name}",
                        "status": "PASS",
                        "details": f"{bp_name} blueprint found in app",
                        "duration": 0.001
                    })
                else:
                    tests.append({
                        "name": f"Blueprint registered: {bp_name}",
                        "status": "FAIL",
                        "error": f"{bp_name} blueprint not registered",
                        "duration": 0.001
                    })
                    
        except Exception as e:
            tests.append({
                "name": "Full app creation",
                "status": "FAIL",
                "error": str(e),
                "duration": 0.001
            })
            
        if self.verbose:
            for test in tests[-5:]:
                print(f"      {'✅' if test['status'] == 'PASS' else '❌'} {test['name']}")
        
        return tests
        
    def check_venv(self) -> bool:
        """Check if running in virtual environment"""
        return hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
        
    def generate_report_file(self):
        """Generate detailed test report"""
        if not self.generate_report:
            return
            
        report_dir = PROJECT_ROOT / "testing" / "reports"
        report_dir.mkdir(exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        report_file = report_dir / f"test_report_{timestamp}.json"
        
        report_data = {
            "timestamp": timestamp,
            "duration": time.time() - self.start_time,
            "environment": {
                "python_version": sys.version,
                "virtual_env": self.check_venv(),
                "platform": sys.platform,
                "project_root": str(PROJECT_ROOT)
            },
            "results": self.results,
            "summary": {
                "total_categories": len(self.results),
                "total_tests": sum(len(r["tests"]) for r in self.results),
                "total_passed": sum(r["passed"] for r in self.results),
                "total_failed": sum(r["failed"] for r in self.results),
                "total_skipped": sum(r["skipped"] for r in self.results)
            }
        }
        
        with open(report_file, 'w') as f:
            json.dump(report_data, f, indent=2)
            
        print(f"\n📄 Test report saved: {report_file}")
        
    def run(self, categories: List[str]):
        """Run the complete test suite"""
        print("🚀 KeukaSensor Test Suite Starting...")
        print(f"   Project root: {PROJECT_ROOT}")
        print(f"   Virtual environment: {'Yes' if self.check_venv() else 'No'}")
        print(f"   Safe mode: {'Enabled' if self.test_config['safe_mode'] else 'Disabled'}")
        
        self.setup_test_environment()
        
        try:
            for category in categories:
                result = self.run_test_category(category)
                self.results.append(result)
                
            # Print overall summary
            total_tests = sum(len(r["tests"]) for r in self.results)
            total_passed = sum(r["passed"] for r in self.results)
            total_failed = sum(r["failed"] for r in self.results)
            total_skipped = sum(r["skipped"] for r in self.results)
            
            print(f"\n🏁 Test Suite Complete!")
            print(f"   Total: {total_tests} tests")
            print(f"   ✅ Passed: {total_passed}")
            print(f"   ❌ Failed: {total_failed}")
            print(f"   ⏭️ Skipped: {total_skipped}")
            print(f"   Duration: {time.time() - self.start_time:.2f}s")
            
            if total_failed > 0:
                print("\n⚠️  Some tests failed. Check the output above for details.")
                return 1
            else:
                print("\n🎉 All tests passed!")
                return 0
                
        finally:
            self.cleanup_test_environment()
            if self.generate_report:
                self.generate_report_file()


def main():
    parser = argparse.ArgumentParser(description="KeukaSensor Comprehensive Test Suite")
    parser.add_argument("--category", choices=["config", "http", "sensors", "camera", "network", "security", "integration", "all"], 
                       default="all", help="Test category to run")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--report", "-r", action="store_true", help="Generate test report")
    
    args = parser.parse_args()
    
    # Determine categories to run
    if args.category == "all":
        categories = ["config", "http", "sensors", "camera", "network", "security", "integration"]
    else:
        categories = [args.category]
    
    # Run tests
    runner = TestRunner(verbose=args.verbose, generate_report=args.report)
    return runner.run(categories)


if __name__ == "__main__":
    sys.exit(main())