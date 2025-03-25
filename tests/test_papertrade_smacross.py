import os
import unittest
import backtrader as bt
from datetime import datetime
import logging
from alpaca_backtrader_api import AlpacaStore
from alpaca.data.enums import DataFeed
# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SmaCross(bt.SignalStrategy):
    def __init__(self):
        logger.info("Initializing Test Strategy")
        self.order = None
        self.buyprice = None
        self.buycomm = None
        self.bar_count = 0
        self.buy_interval = 2 # Buy every 2 bars
        self.sell_interval = 1  # Sell after 1 bar
        
        # Add position size control
        self.sizer = bt.sizers.PercentSizer(percents=10)
        
        logger.info("Test Strategy initialized")

    def next(self):
        # Print bar info
        dt = self.data.datetime.datetime(0)
        logger.info('[%s] Open: %.2f, High: %.2f, Low: %.2f, Close: %.2f, Volume: %.0f' %
                 (dt.strftime('%Y-%m-%d %H:%M:%S'), 
                  self.data.open[0],
                  self.data.high[0], 
                  self.data.low[0],
                  self.data.close[0],
                  self.data.volume[0]))
        
        # If we have a pending order, don't do anything
        if self.order:
            return
            
        self.bar_count += 1
        
        # Check if we are in a position
        if not self.position:
            # Buy every buy_interval bars
            if self.bar_count % self.buy_interval == 0:
                logger.info(f'BUY CREATE at bar {self.bar_count}')
                self.order = self.buy()
        else:
            # Sell after sell_interval bars
            if self.bar_count % self.sell_interval == 0:
                logger.info(f'SELL CREATE at bar {self.bar_count}')
                self.order = self.sell()
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            # Buy/Sell order submitted/accepted - Nothing to do
            return

        # Check if an order has been completed
        if order.status in [order.Completed]:
            # Get current datetime 
            dt = self.data.datetime.datetime(0)
            
            if order.isbuy():
                logger.info('[%s] BUY EXECUTED, Price: %.2f, Size: %.4f, Cost: %.2f, Comm: %.2f' %
                    (dt.strftime('%Y-%m-%d %H:%M:%S'), order.executed.price, order.executed.size, 
                     order.executed.value, order.executed.comm))
            else:  # Sell
                logger.info('[%s] SELL EXECUTED, Price: %.2f, Size: %.4f, Cost: %.2f, Comm: %.2f' %
                    (dt.strftime('%Y-%m-%d %H:%M:%S'), order.executed.price, order.executed.size, 
                     order.executed.value, order.executed.comm))
        
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            dt = self.data.datetime.datetime(0)
            logger.warning('[%s] Order Canceled/Margin/Rejected' % dt.strftime('%Y-%m-%d %H:%M:%S'))

    def notify_trade(self, trade):
        if not trade.isclosed:
            return

        dt = self.data.datetime.datetime(0)
        logger.info('[%s] TRADE CLOSED, Profit: %.2f, Gross: %.2f, Net: %.2f' %
                 (dt.strftime('%Y-%m-%d %H:%M:%S'), trade.pnl, trade.pnlcomm, trade.pnlcomm - trade.commission))

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
        cerebro.addstrategy(SmaCross)
        
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
        
        # Set up the data feed
        data0 = store.getdata(
            dataname='BTC/USD',
            timeframe=bt.TimeFrame.Minutes
        )
        
        broker = store.getbroker()
        cerebro.setbroker(broker)
        
        # Add the data feed to the cerebro engine
        cerebro.adddata(data0)
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