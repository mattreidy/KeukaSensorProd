#!/bin/bash
# Virtual Environment Setup Script for Testing
# Creates an isolated test environment for KeukaSensor testing

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTING_DIR="$PROJECT_ROOT/testing"
VENV_DIR="$TESTING_DIR/test_venv"

echo "🔧 Setting up test virtual environment..."
echo "   Project root: $PROJECT_ROOT"
echo "   Test venv: $VENV_DIR"

# Remove existing test venv if it exists
if [ -d "$VENV_DIR" ]; then
    echo "   Removing existing test environment..."
    rm -rf "$VENV_DIR"
fi

# Create new virtual environment
echo "   Creating virtual environment..."
python3 -m venv "$VENV_DIR"

# Activate virtual environment
echo "   Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Upgrade pip
echo "   Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "   Installing requirements..."
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    pip install -r "$PROJECT_ROOT/requirements.txt"
else
    echo "   Warning: requirements.txt not found, installing basic dependencies..."
    pip install flask pytest requests
fi

# Install additional testing dependencies
echo "   Installing testing dependencies..."
pip install \
    pytest-flask \
    pytest-asyncio \
    pytest-cov \
    requests-mock \
    mock

# Create test runner script
echo "   Creating test runner script..."
cat > "$TESTING_DIR/run_tests.sh" << 'EOF'
#!/bin/bash
# Test runner script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/test_venv"

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo "❌ Test virtual environment not found. Run test_venv_setup.sh first."
    exit 1
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Set test environment
export KEUKA_TEST_MODE=1
export KEUKA_MOCK_HARDWARE=1
export KEUKA_SAFE_MODE=1

# Run tests
echo "🚀 Running KeukaSensor test suite..."
python "$SCRIPT_DIR/run_tests.py" "$@"
EOF

chmod +x "$TESTING_DIR/run_tests.sh"

# Create pytest configuration
echo "   Creating pytest configuration..."
cat > "$TESTING_DIR/pytest.ini" << 'EOF'
[tool:pytest]
testpaths = unit integration
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = 
    -v
    --tb=short
    --strict-markers
    --disable-warnings
markers =
    unit: Unit tests
    integration: Integration tests
    slow: Slow tests that may take longer
    hardware: Tests that require hardware (mocked in CI)
    network: Tests that require network access
EOF

# Create coverage configuration
echo "   Creating coverage configuration..."
cat > "$TESTING_DIR/.coveragerc" << 'EOF'
[run]
source = ../keuka
omit = 
    */test*
    */testing/*
    */venv/*
    */env/*

[report]
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
    if __name__ == .__main__.:
    if TYPE_CHECKING:
EOF

# Create test summary script
echo "   Creating test summary script..."
cat > "$TESTING_DIR/test_summary.py" << 'EOF'
#!/usr/bin/env python3
"""
Generate test summary report
"""
import json
import sys
from pathlib import Path
from datetime import datetime

def generate_summary():
    reports_dir = Path(__file__).parent / "reports"
    
    if not reports_dir.exists():
        print("No test reports found")
        return
        
    # Find most recent report
    reports = list(reports_dir.glob("test_report_*.json"))
    if not reports:
        print("No test reports found")
        return
        
    latest_report = max(reports, key=lambda p: p.stat().st_mtime)
    
    with open(latest_report) as f:
        data = json.load(f)
    
    print(f"📊 Test Summary Report")
    print(f"   Report: {latest_report.name}")
    print(f"   Generated: {data['timestamp']}")
    print(f"   Duration: {data['duration']:.2f}s")
    print()
    
    summary = data['summary']
    print(f"📈 Overall Results:")
    print(f"   Categories: {summary['total_categories']}")
    print(f"   Tests: {summary['total_tests']}")
    print(f"   ✅ Passed: {summary['total_passed']}")
    print(f"   ❌ Failed: {summary['total_failed']}")
    print(f"   ⏭️ Skipped: {summary['total_skipped']}")
    
    if summary['total_failed'] > 0:
        print("\n🔍 Failed Tests:")
        for result in data['results']:
            failed_tests = [t for t in result['tests'] if t['status'] == 'FAIL']
            if failed_tests:
                print(f"   {result['category'].upper()}:")
                for test in failed_tests:
                    print(f"     ❌ {test['name']}")
                    if 'error' in test:
                        print(f"        Error: {test['error']}")

if __name__ == "__main__":
    generate_summary()
EOF

chmod +x "$TESTING_DIR/test_summary.py"

echo "✅ Test environment setup complete!"
echo ""
echo "🎯 Usage:"
echo "   Run all tests:     ./testing/run_tests.sh"
echo "   Run specific test: ./testing/run_tests.sh --category http"
echo "   Generate report:   ./testing/run_tests.sh --report"
echo "   View summary:      python testing/test_summary.py"
echo ""
echo "📋 Test Categories:"
echo "   config       - Configuration validation"
echo "   http         - HTTP endpoint testing"  
echo "   sensors      - Sensor functionality (mocked)"
echo "   camera       - Camera functionality (mocked)"
echo "   network      - Network utilities (safe)"
echo "   security     - Authentication and security"
echo "   integration  - End-to-end workflows"
echo ""
echo "🔒 Safety Features:"
echo "   ✅ Hardware mocking enabled"
echo "   ✅ Test mode environment"
echo "   ✅ Safe configuration isolation"
echo "   ✅ No production system modifications"