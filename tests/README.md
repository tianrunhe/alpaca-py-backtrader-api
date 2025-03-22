# Alpaca Backtrader API Tests

This directory contains tests for the Alpaca Backtrader API.

## Test Types

- **Unit Tests**: Basic tests that check individual components without external dependencies.
- **Integration Tests**: Tests that require Alpaca API credentials and integrate with the Alpaca platform.

## Running Unit Tests

Unit tests can be run with:

```bash
python -m unittest tests/test_alpacabroker.py
```

## Running Integration Tests

Integration tests require Alpaca API credentials to be set as environment variables.

### Using the Script

The easiest way to run integration tests is to use the provided script:

```bash
ALPACA_API_KEY=<your-key> ALPACA_SECRET_KEY=<your-secret> ./run_integration_tests.sh
```

### Manually Running Tests

You can also run specific integration tests manually:

```bash
# Set environment variables
export ALPACA_API_KEY=<your-key>
export ALPACA_SECRET_KEY=<your-secret>

# Run backtest integration test
python -m unittest tests/test_smacross_strategy.py

# Run paper trade setup test
python -m unittest tests/test_papertrade_smacross.py
```

## Notes

- Integration tests require valid Alpaca API credentials
- The backtest integration test runs a full backtest using historical data
- The paper trade setup test only checks that the setup is correct without running real-time data processing or submitting orders
- If you don't have credentials, these tests will be skipped

## Creating New Tests

When creating new tests, follow these guidelines:

1. Unit tests should not require external API access
2. Integration tests should skip gracefully if API credentials are not provided
3. Tests should be designed to run quickly and should not make excessive API calls
4. Tests should clean up after themselves (no lingering orders, positions, etc.) 