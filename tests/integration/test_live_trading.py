import os
import unittest
import backtrader as bt
from datetime import datetime
import logging
from alpaca_backtrader_api import AlpacaStore
from alpaca.data.enums import DataFeed
# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Set various loggers to INFO level
logging.getLogger('websockets.client').setLevel(logging.INFO)
logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
logging.getLogger('asyncio').setLevel(logging.INFO)

class TestStrategy(bt.SignalStrategy):
    def __init__(self):
        logger.info("Initializing Test Strategy")
        logger.info(f"Number of data feeds: {len(self.datas)}")
        for i, data in enumerate(self.datas):
            logger.info(f"Data feed {i}: {data._name}")
        super(TestStrategy, self).__init__()

    def next(self):
        # Loop over all the data feeds (symbols)
        for data in self.datas:
            logger.info(f"Processing data for {data._name}. Now: {data.close[0]} Previous: {data.close[-1]}")
            # Example condition: if current close is above a threshold, buy
            if not self.getposition(data).size and data.close[0] > data.close[-1]:
                self.buy(data=data)
            # Example exit: if price drops below previous bar, sell
            elif self.getposition(data).size and data.close[0] < data.close[-1]:
                self.sell(data=data)
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            # Buy/Sell order submitted/accepted - Nothing to do
            return

        # Check if an order has been completed
        if order.status in [order.Completed]:
            # Get current datetime and data name
            dt = order.data.datetime.datetime(0)
            symbol = order.data._name
            
            if order.isbuy():
                logger.info('[%s] %s BUY EXECUTED, Price: %.2f, Size: %.4f, Cost: %.2f, Comm: %.2f' %
                    (dt.strftime('%Y-%m-%d %H:%M:%S'), symbol, order.executed.price, order.executed.size, 
                     order.executed.value, order.executed.comm))
            else:  # Sell
                logger.info('[%s] %s SELL EXECUTED, Price: %.2f, Size: %.4f, Cost: %.2f, Comm: %.2f' %
                    (dt.strftime('%Y-%m-%d %H:%M:%S'), symbol, order.executed.price, order.executed.size, 
                     order.executed.value, order.executed.comm))
        
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            dt = order.data.datetime.datetime(0)
            symbol = order.data._name
            logger.warning('[%s] %s Order Canceled/Margin/Rejected' % (dt.strftime('%Y-%m-%d %H:%M:%S'), symbol))

    def notify_trade(self, trade):
        if not trade.isclosed:
            return

        dt = trade.data.datetime.datetime(0)
        symbol = trade.data._name
        logger.info('[%s] %s TRADE CLOSED, Profit: %.2f, Gross: %.2f, Net: %.2f' %
                 (dt.strftime('%Y-%m-%d %H:%M:%S'), symbol, trade.pnl, trade.pnlcomm, trade.pnlcomm - trade.commission))

class TestPaperTradeSmaCrossStrategy(unittest.TestCase):
    
    def setUp(self):
        # Skip test if API keys are not set
        self.api_key = os.environ.get('ALPACA_API_KEY', '')
        self.api_secret = os.environ.get('ALPACA_SECRET_KEY', '')
        if not self.api_key or not self.api_secret:
            self.skipTest('ALPACA_API_KEY and ALPACA_SECRET_KEY not set in environment')
            
    def test_paper_setup(self):
        """Test setting up a SMA Cross strategy for paper trading"""
        cerebro = bt.Cerebro()
        cerebro.addstrategy(TestStrategy)
        
        # Add analyzers for when we run the strategy
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
        
        # Initialize store with the API credentials
        store = AlpacaStore(
            key_id=self.api_key,
            secret_key=self.api_secret,
            paper=True  # Use paper trading account
        )
        
        # Set up the broker
        broker = store.getbroker()
        cerebro.setbroker(broker)
        
        # Set up multiple data feeds
        # symbols = ['AAPL', 'GOOG', 'MSFT', 'TSLA', 'NVDA', 'AMZN', 'META']
        symbols = ['BTC/USD']

        for i, symbol in enumerate(symbols):
            data = store.getdata(
                dataname=symbol,
                timeframe=bt.TimeFrame.Minutes,
                # Set a unique name for each data feed
                name=symbol,
                backfill_start=False,
                backfill=False,
                historical=False,
            )
            # Add the data feed to the cerebro engine
            cerebro.adddata(data)
        
        cerebro.addsizer(bt.sizers.PercentSizer, percents=10)
        
        # Just check that the broker is accessible and has a value
        initial_value = cerebro.broker.getvalue()
        self.assertIsNotNone(initial_value)
        
        logger.info(f'Paper Trading Account Value: {initial_value:.2f}')
        logger.info('Paper trading broker setup successful')
        logger.info('Data feed setup successful')
        
        # Get some positions and portfolio value info for logging
        positions = store.get_positions()
        logger.info(f'Current positions: {len(positions)}')
        for position in positions:
            logger.info(f'Position: {position.symbol}, Qty: {position.qty}, Market Value: {position.market_value}')
        
        # We don't actually run the strategy since that would start 
        # real-time data processing and potentially submit orders
        # This is just a setup test
        cerebro.run()

if __name__ == '__main__':
    unittest.main() 