import os
import pytest
import backtrader as bt
from datetime import datetime
from alpaca.data.enums import DataFeed
from alpaca_backtrader_api import AlpacaStore
import pytz

class BuyAndHoldStrategy(bt.Strategy):
    """A strategy that buys at market open and sells at market close."""
    
    def __init__(self):
        self.order = None
        self.buy_price = None
        self.shares = 10  # Fixed number of shares to buy
        
    def next(self):
        # Get current bar's datetime in NY timezone
        current_dt = self.data.datetime.datetime(0)

        # Buy at market open
        if not self.position and current_dt.hour == 9:
            self.order = self.buy(size=self.shares)
            print(f'BUY CREATE at {current_dt}, Price: {self.data.close[0]:.2f}, Size: {self.shares}')
        
        # Sell at market close
        elif self.position and current_dt.hour == 15:
            self.order = self.sell(size=self.shares)
            print(f'SELL CREATE at {current_dt}, Price: {self.data.close[0]:.2f}, Size: {self.shares}')
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        if order.status in [order.Completed]:
            if order.isbuy():
                self.buy_price = order.executed.price
                print(f'BUY EXECUTED, Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}')
            else:  # Sell
                print(f'SELL EXECUTED, Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}')
        
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            print('Order Canceled/Margin/Rejected')

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

def test_backtest_buy_and_hold(api_credentials, capsys):
    """Test strategy that buys at market open and sells at market close"""
    api_key, api_secret = api_credentials
    cerebro = bt.Cerebro()
    cerebro.addstrategy(BuyAndHoldStrategy)
    
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
    
    # Set up historical data with precise NY timezone times
    ny_tz = pytz.timezone('America/New_York')
    fromdate = ny_tz.localize(datetime(2025, 1, 2))
    todate = ny_tz.localize(datetime(2025, 1, 3))
    
    DataFactory = store.getdata
    data0 = DataFactory(
        dataname='AAPL',
        historical=True,
        fromdate=fromdate,
        todate=todate,
        timeframe=bt.TimeFrame.Minutes,
        data_feed=DataFeed.IEX
    )
    cerebro.adddata(data0)
    
    # Set initial cash
    cerebro.broker.setcash(100000.0)
    
    # Run backtest
    initial_value = cerebro.broker.getvalue()
    results = cerebro.run()
    final_value = cerebro.broker.getvalue()
    
    # Extract strategy and analyzer results
    strat = results[0]
    trade_analysis = strat.analyzers.trades.get_analysis()
    
    # Test 1: Data Loading
    # Verify we have data points
    assert len(data0) > 0, "No data points loaded"
    
    # Test 2: Order Processing
    # Verify we have exactly one complete trade (buy-sell cycle)
    total_trades = trade_analysis.get('total', {}).get('total', 0)
    assert total_trades == 1, f"Expected exactly 1 complete trade, got {total_trades}"
    
    # Test 3: Position Management
    # Verify we have no position at the end (since we sold)
    assert strat.position.size == 0, "Position should be closed at the end of backtest"
    
    # Test 4: Portfolio Value
    # Verify portfolio value is not None and less than initial value (since we know AAPL closed lower)
    assert final_value is not None, "Final portfolio value is None"
    assert final_value < initial_value, "Final portfolio value should be less than initial value"
    
    # Test 5: Trade Analysis
    # We expect:
    # - One complete trade (buy-sell cycle)
    # - No open trades (since we sold)
    # - One lost trade (since AAPL closed lower)
    # - No won trades
    assert trade_analysis.get('total', {}).get('total', 0) == 1, "Expected exactly one complete trade"
    assert trade_analysis.get('total', {}).get('open', 0) == 0, "Expected no open trades"
    assert trade_analysis.get('won', {}).get('total', 0) == 0, "Expected no won trades"
    assert trade_analysis.get('lost', {}).get('total', 0) == 1, "Expected one lost trade"
    
    # Log results for debugging
    print('------ Test Results ------')
    print(f'Initial Portfolio Value: {initial_value:.2f}')
    print(f'Final Portfolio Value: {final_value:.2f}')
    print(f'Total Return: {((final_value / initial_value) - 1) * 100:.2f}%')
    
    # Log trade details
    if strat.buy_price:
        print(f'Buy Price: {strat.buy_price:.2f}')
        print(f'Final Price: {data0.close[0]:.2f}')
        print(f'Position Size: {strat.position.size}')
        print(f'Position Value: {strat.position.size * data0.close[0]:.2f}')
    
    # If plotting is needed during development:
    # cerebro.plot() # Uncomment for visual inspection 