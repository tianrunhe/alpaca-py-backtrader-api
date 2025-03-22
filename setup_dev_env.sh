#!/bin/bash
# Setup script for alpaca-backtrader-api development with Python 3.10.16

# Check if Python 3.10 is installed
if ! command -v python3.10 &> /dev/null; then
    echo "Python 3.10 is not installed. Please install Python 3.10 first."
    echo "You can download it from https://www.python.org/downloads/"
    exit 1
fi

# Check Python version
python_version=$(python3.10 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ "$python_version" != "3.10" ]]; then
    echo "Python version $python_version detected. This project requires Python 3.10.x"
    exit 1
fi

# Create virtual environment
echo "Creating a virtual environment with Python 3.10..."
python3.10 -m venv venv
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "Installing development dependencies..."
pip install -r requirements/requirements.txt
pip install -r requirements/requirements_test.txt

# Install the package in development mode
echo "Installing alpaca-backtrader-api in development mode..."
pip install -e .

echo "Development environment setup complete!"
echo "To activate the environment, run:"
echo "  source venv/bin/activate" 