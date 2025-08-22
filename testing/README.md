# KeukaSensor Testing Framework

## Overview

This comprehensive testing framework provides safe, isolated testing for all aspects of the KeukaSensor application without affecting the production environment.

## 🚀 Quick Start

### 1. Set up test environment
```bash
# Create isolated test virtual environment
./testing/test_venv_setup.sh
```

### 2. Run tests
```bash
# Run all tests
./testing/run_tests.sh

# Run specific category
./testing/run_tests.sh --category http

# Run with verbose output and generate report
./testing/run_tests.sh --verbose --report
```

### 3. View results
```bash
# View summary of latest test run
python testing/test_summary.py
```

## 🧪 Test Categories

### Configuration Tests (`config`)
- Validates configuration file existence and structure
- Tests configuration module imports
- Checks environment templates
- **Safe**: Only reads configuration files, no modifications

### HTTP Endpoint Tests (`http`) 
- Tests all HTTP endpoints using Flask test client
- Validates response formats and status codes
- Tests authentication requirements
- **Safe**: Uses isolated Flask test client, no network requests

### Sensor Tests (`sensors`)
- Tests sensor module imports and functionality
- Uses hardware mocking for safe testing
- Validates sensor data formats
- **Safe**: All hardware operations are mocked

### Camera Tests (`camera`)
- Tests camera module functionality
- Mocks OpenCV and camera hardware
- Validates image processing pipelines
- **Safe**: Camera hardware fully mocked

### Network Tests (`network`)
- Tests networking utilities in safe mode
- Validates function existence without execution
- Tests data format handling
- **Safe**: No actual network operations performed

### Security Tests (`security`)
- Tests authentication and authorization
- Validates security module imports
- Tests admin and SSH functionality
- **Safe**: No actual security modifications

### Integration Tests (`integration`)
- End-to-end workflow testing
- Tests full application startup
- Validates component integration
- **Safe**: Uses test environment isolation

## 🔒 Safety Features

### Hardware Mocking
- **RPi.GPIO**: Complete GPIO operation mocking
- **w1thermsensor**: Temperature sensor mocking with realistic values
- **OpenCV**: Camera capture mocking with synthetic images
- **Serial**: GPS serial communication mocking

### Environment Isolation
- Temporary configuration directories
- Test-specific environment variables
- Isolated virtual environment
- No production file modifications

### Safe Mode Operations
- `KEUKA_TEST_MODE=1`: Enables test mode throughout application
- `KEUKA_MOCK_HARDWARE=1`: Forces hardware mocking
- `KEUKA_SAFE_MODE=1`: Prevents dangerous operations
- Automatic cleanup of temporary files

## 📊 Test Reporting

### Standard Output
```
🚀 KeukaSensor Test Suite Starting...
📋 Running CONFIG tests...
      ✅ Config file exists: config.py
      ✅ Import core config module
   ✅ 5/5 passed, ❌ 0 failed, ⏭️ 0 skipped (0.12s)
🏁 Test Suite Complete!
```

### JSON Reports
Detailed reports saved to `testing/reports/test_report_YYYYMMDD_HHMMSS.json`:
```json
{
  "timestamp": "20230822_103000",
  "duration": 15.23,
  "environment": {
    "python_version": "3.9.2",
    "virtual_env": true,
    "platform": "linux"
  },
  "results": [...],
  "summary": {
    "total_tests": 45,
    "total_passed": 43,
    "total_failed": 2
  }
}
```

## 🛠️ Development Workflow

### Adding New Tests
1. Create test file in appropriate category directory:
   ```python
   # testing/unit/test_new_feature.py
   import pytest
   from testing.fixtures.test_configs import create_full_test_environment
   
   class TestNewFeature:
       def setup_method(self):
           self.config_manager = create_full_test_environment()
       
       def teardown_method(self):
           self.config_manager.cleanup_temp_dirs()
           self.config_manager.restore_environment()
       
       def test_new_functionality(self):
           # Your test here
           pass
   ```

2. Run specific test:
   ```bash
   ./testing/run_tests.sh --category unit
   ```

### Debugging Failed Tests
1. Run with verbose output:
   ```bash
   ./testing/run_tests.sh --verbose
   ```

2. Check detailed report:
   ```bash
   python testing/test_summary.py
   ```

3. Run individual test file:
   ```bash
   source testing/test_venv/bin/activate
   python -m pytest testing/unit/test_new_feature.py -v
   ```

## 📁 Directory Structure

```
testing/
├── README.md                    # This file
├── run_tests.py                 # Main test runner
├── run_tests.sh                 # Shell wrapper script
├── test_venv_setup.sh          # Environment setup
├── pytest.ini                  # Pytest configuration
├── .coveragerc                 # Coverage configuration
├── test_summary.py             # Report generator
├── unit/                       # Unit tests
│   ├── test_config.py
│   └── test_sensors.py
├── integration/                # Integration tests  
│   └── test_http_endpoints.py
├── fixtures/                   # Test fixtures and utilities
│   └── test_configs.py
├── mocks/                      # Hardware mocking utilities
│   └── hardware_mocks.py
└── reports/                    # Generated test reports
    └── test_report_*.json
```

## 🔧 Advanced Usage

### Custom Test Environment
```python
from testing.fixtures.test_configs import TestConfigManager

config_manager = TestConfigManager()
temp_dir = config_manager.create_temp_config_dir()

# Create custom configuration
config_manager.create_test_sensor_config(temp_dir)
config_manager.set_test_environment(temp_dir)

# Run your tests...

config_manager.cleanup_temp_dirs()
```

### Hardware Mock Customization
```python
from testing.mocks.hardware_mocks import HardwareMockManager

mock_manager = HardwareMockManager()
mock_manager.activate_mocks()

# Your tests with mocked hardware...

mock_manager.deactivate_mocks()
```

### Running in CI/CD
```yaml
# Example GitHub Actions
- name: Run KeukaSensor Tests
  run: |
    ./testing/test_venv_setup.sh
    ./testing/run_tests.sh --report
    
- name: Upload Test Reports
  uses: actions/upload-artifact@v3
  with:
    name: test-reports
    path: testing/reports/
```

## ⚠️ Important Notes

### Production Safety
- **Never run tests on production hardware without `KEUKA_TEST_MODE=1`**
- Always verify `KEUKA_SAFE_MODE=1` is set for production system testing
- Test environment is automatically isolated from production configurations

### Hardware Requirements
- Tests run on any Python 3.9+ system
- No GPIO hardware required (mocked)
- No camera hardware required (mocked)
- No sensor hardware required (mocked)

### Virtual Environment
- Tests use isolated virtual environment in `testing/test_venv/`
- Production virtual environment remains untouched
- All test dependencies installed separately

## 🐛 Troubleshooting

### Common Issues

**"Test virtual environment not found"**
```bash
./testing/test_venv_setup.sh
```

**"Module import errors"**
- Ensure test virtual environment is activated
- Check that requirements.txt is complete
- Verify PYTHONPATH includes keuka package

**"Hardware access errors in tests"** 
- Verify `KEUKA_TEST_MODE=1` is set
- Check that hardware mocks are activated
- Ensure `KEUKA_MOCK_HARDWARE=1` is set

**"Permission errors"**
- Check file permissions on test scripts
- Ensure write access to `testing/` directory
- Verify temporary directory cleanup

### Getting Help

1. Check test output for specific error messages
2. Review `testing/reports/` for detailed failure information  
3. Run individual test files with pytest for detailed debugging
4. Verify all safety environment variables are set correctly