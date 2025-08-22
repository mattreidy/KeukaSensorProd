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