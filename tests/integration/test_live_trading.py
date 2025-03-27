import os
import pytest
import backtrader as bt
from datetime import datetime
import logging
import time
from alpaca_backtrader_api import AlpacaStore
from alpaca.data.enums import DataFeed

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Set various loggers to INFO level to reduce noise
logging.getLogger('websockets.client').setLevel(logging.INFO)
logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
logging.getLogger('asyncio').setLevel(logging.INFO)

class DeterministicCryptoStrategy(bt.Strategy):
    """A simplified strategy that buys at first bar, sells at second, and exits at third."""
    
    def __init__(self):
        self.order = None
        self.buy_price = None
        self.bar_count = 0
        self.data_received = False
        self.buy_order_submitted = False
        self.sell_order_submitted = False
        print('Strategy initialized - Will buy on bar 1, sell on bar 2, exit on bar 3')
    
    def next(self):
        self.bar_count += 1
        self.data_received = True
        current_dt = self.data.datetime.datetime(0)
        
        print(f'Bar #{self.bar_count} at {current_dt}: Price: {self.data.close[0]:.2f}')
        
        # Buy on first bar - only submit order if we haven't already
        if self.bar_count == 1 and not self.buy_order_submitted:
            self.order = self.buy()  # Small fixed size for testing
            self.buy_order_submitted = True
            print(f'BUY ORDER CREATED at bar #{self.bar_count}, Price: {self.data.close[0]:.2f}')
        
        # Sell on second bar - regardless of whether buy executed (the order won't execute if no position exists)
        elif self.bar_count == 2 and not self.sell_order_submitted:
            self.order = self.sell()  # Use same fixed size
            self.sell_order_submitted = True
            print(f'SELL ORDER CREATED at bar #{self.bar_count}, Price: {self.data.close[0]:.2f}')
        
        # Exit on third bar
        elif self.bar_count >= 3:
            print(f'Reached maximum bars (3). Stopping strategy.')
            self.env.runstop()
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            # Log when order is submitted/accepted
            print(f"Order {order.ref} {order.getstatusname()}")
            return

        if order.status in [order.Completed]:
            if order.isbuy():
                self.buy_price = order.executed.price
                print(f'BUY EXECUTED, Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}, Size: {order.executed.size}')
            else:  # Sell
                print(f'SELL EXECUTED, Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}, Size: {order.executed.size}')
            self.order = None
        
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            print(f'Order {order.ref} {order.getstatusname()}')
            self.order = None

    def notify_trade(self, trade):
        if not trade.isclosed:
            return

        print(f'TRADE PROFIT, Gross: {trade.pnl:.2f}, Net: {trade.pnlcomm:.2f}')

@pytest.fixture
def api_credentials():
    """Fixture to provide API credentials and skip if not available"""
    api_key = os.environ.get('ALPACA_API_KEY', '')
    api_secret = os.environ.get('ALPACA_SECRET_KEY', '')
    if not api_key or not api_secret:
        pytest.skip('ALPACA_API_KEY and ALPACA_SECRET_KEY not set in environment')
    return api_key, api_secret

def test_live_crypto_trading(api_credentials, capsys):
    """Test a deterministic strategy for live crypto trading"""
    api_key, api_secret = api_credentials
    
    cerebro = bt.Cerebro()
    cerebro.addstrategy(DeterministicCryptoStrategy)
    
    # Add analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    
    # Initialize store with the API credentials
    store = AlpacaStore(
        key_id=api_key,
        secret_key=api_secret,
        paper=True  # Use paper trading account
    )
    
    # Set up the broker
    broker = store.getbroker()
    cerebro.setbroker(broker)
    
    # Set up data feed for BTC/USD
    data = store.getdata(
        dataname='BTC/USD',
        timeframe=bt.TimeFrame.Minutes,
        name='BTC/USD',
        backfill_start=False,
        backfill=False,
        historical=False,
    )
    
    # Add the data feed to the cerebro engine
    cerebro.adddata(data)
    
    # Set position size to a percentage of portfolio
    cerebro.addsizer(bt.sizers.PercentSizer, percents=1)  # Use small percentage for testing
    
    # Get initial portfolio state
    initial_value = cerebro.broker.getvalue()
    print(f'Initial Portfolio Value: {initial_value:.2f}')
    
    # Get initial positions
    positions_before = store.get_positions()
    print(f'Initial positions: {len(positions_before)}')
    
    # Run the strategy with a timeout
    print("Starting strategy execution...")
    results = cerebro.run()
    
    # Get final portfolio state
    final_value = cerebro.broker.getvalue()
    print(f'Final Portfolio Value: {final_value:.2f}')
    
    # Extract strategy and analyzer results
    strat = results[0]
    trade_analysis = strat.analyzers.trades.get_analysis()
    
    # Get final positions
    positions_after = store.get_positions()
    
    # Assertions
    
    # Test 1: Data reception
    assert strat.data_received, "No data was received from the live feed"
    
    # Test 2: Bar processing
    assert strat.bar_count > 0, "No bars were processed"
    
    # Test 3: Initial broker value
    assert initial_value > 0, "Initial portfolio value should be positive"
    
    # Test 4: Final broker value 
    assert final_value != initial_value, "Final portfolio value should be different from initial value"
    
    # Test 5: Order execution verification
    # In live trading, we can't guarantee orders will be filled, so instead check that orders were submitted
    assert strat.buy_order_submitted, "Buy order was not submitted"
    assert strat.sell_order_submitted, "Sell order was not submitted"
    
    # Instead of checking trades analysis (which only shows completed trades)
    # we can check if position size changed during the test
    print('------ Test Results ------')
    print(f'Bars processed: {strat.bar_count}')
    print(f'Buy order submitted: {strat.buy_order_submitted}')
    print(f'Sell order submitted: {strat.sell_order_submitted}')
    
    if strat.buy_price:
        print(f'Buy Price: {strat.buy_price:.2f}')
    
    # Print trade analysis if available
    if trade_analysis:
        total_trades = trade_analysis.get('total', {}).get('total', 0)
        print(f'Total trades executed: {total_trades}') 