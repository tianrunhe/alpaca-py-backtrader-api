#!/bin/bash

# Exit on error
set -e

# Check if API keys are provided
if [ -z "$ALPACA_API_KEY" ] || [ -z "$ALPACA_SECRET_KEY" ]; then
  echo "Error: ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables must be set."
  echo "Usage:"
  echo "  ALPACA_API_KEY=<your-key> ALPACA_SECRET_KEY=<your-secret> ./run_integration_tests.sh"
  exit 1
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
  echo "Activating virtual environment..."
  source venv/bin/activate
fi

# Run the integration tests
echo "Running backtest integration test..."
python -m unittest tests/test_smacross_strategy.py

# echo "Running paper trade setup test..."
# python -m unittest tests/test_papertrade_smacross.py

echo "Integration tests completed successfully!" 