import os
import unittest
import backtrader as bt
from datetime import datetime
import logging
from alpaca.data.enums import DataFeed
from alpaca_backtrader_api import AlpacaStore

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SmaCross(bt.SignalStrategy):
    def __init__(self):
        sma1, sma2 = bt.ind.SMA(period=10), bt.ind.SMA(period=30)
        crossover = bt.ind.CrossOver(sma1, sma2)
        self.signal_add(bt.SIGNAL_LONG, crossover)

    def next(self):
        dt = self.data.datetime.datetime(0)
        logger.info('[%s] Data received - Open: %.2f, High: %.2f, Low: %.2f, Close: %.2f, Volume: %.0f' %
                  (dt.strftime('%Y-%m-%d %H:%M:%S'), 
                   self.data.open[0], self.data.high[0], 
                   self.data.low[0], self.data.close[0], 
                   self.data.volume[0]))
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            # Buy/Sell order submitted/accepted - Nothing to do
            return

        # Check if an order has been completed
        if order.status in [order.Completed]:
            # Get current datetime 
            dt = self.data.datetime.datetime(0)
            
            if order.isbuy():
                logger.info('[%s] BUY EXECUTED, Price: %.2f, Size: %.2f, Cost: %.2f, Comm: %.2f' %
                    (dt.strftime('%Y-%m-%d %H:%M:%S'), order.executed.price, order.executed.size, 
                     order.executed.value, order.executed.comm))
            else:  # Sell
                logger.info('[%s] SELL EXECUTED, Price: %.2f, Size: %.2f, Cost: %.2f, Comm: %.2f' %
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

class TestSmaCrossStrategy(unittest.TestCase):
    
    def setUp(self):
        # Skip test if API keys are not set
        self.api_key = os.environ.get('ALPACA_API_KEY', '')
        self.api_secret = os.environ.get('ALPACA_SECRET_KEY', '')
        if not self.api_key or not self.api_secret:
            self.skipTest('ALPACA_API_KEY and ALPACA_SECRET_KEY not set in environment')
            
    def test_backtest_smacross(self):
        """Test SMA Cross strategy in backtest mode"""
        cerebro = bt.Cerebro()
        cerebro.addstrategy(SmaCross)
        
        # Add analyzers
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
        
        # Set up historical data
        DataFactory = store.getdata
        data0 = DataFactory(
            dataname='AAPL',
            historical=True,
            fromdate=datetime(2022, 1, 3),
            todate=datetime(2022, 1, 4),
            timeframe=bt.TimeFrame.Minutes,
            data_feed=DataFeed.IEX
        )
        cerebro.adddata(data0)
        cerebro.addsizer(bt.sizers.PercentSizer, percents=10)
        
        # Run backtest
        initial_value = cerebro.broker.getvalue()
        results = cerebro.run()
        final_value = cerebro.broker.getvalue()
        
        # Extract and log strategy results
        strat = results[0]
        
        # Print analyzers results - handle possible None values
        sharpe_ratio = strat.analyzers.sharpe.get_analysis()
        if 'sharperatio' in sharpe_ratio and sharpe_ratio['sharperatio'] is not None:
            logger.info('Sharpe Ratio: %.2f' % sharpe_ratio['sharperatio'])
        else:
            logger.info('Sharpe Ratio: N/A')
        
        drawdown = strat.analyzers.drawdown.get_analysis()
        if 'max' in drawdown and 'drawdown' in drawdown['max']:
            logger.info('Max Drawdown: %.2f%%' % drawdown['max']['drawdown'])
        else:
            logger.info('Max Drawdown: N/A')
        
        returns = strat.analyzers.returns.get_analysis()
        if 'ravg' in returns and returns['ravg'] is not None:
            logger.info('Annual Return: %.2f%%' % (returns['ravg'] * 100))
        else:
            logger.info('Annual Return: N/A')
        
        # Log trade statistics with safer access
        trade_analysis = strat.analyzers.trades.get_analysis()
        logger.info('------ Trade Statistics ------')
        total_trades = trade_analysis.get('total', {}).get('total', 0)
        winning_trades = trade_analysis.get('won', {}).get('total', 0)
        losing_trades = trade_analysis.get('lost', {}).get('total', 0)
        
        logger.info('Total Trades: %d' % total_trades)
        logger.info('Winning Trades: %d' % winning_trades)
        logger.info('Losing Trades: %d' % losing_trades)
        
        if winning_trades > 0:
            avg_win = trade_analysis.get('won', {}).get('pnl', {}).get('average', 0)
            logger.info('Average Profit on Winning Trades: %.2f' % avg_win)
        
        if losing_trades > 0:
            avg_loss = trade_analysis.get('lost', {}).get('pnl', {}).get('average', 0)
            logger.info('Average Loss on Losing Trades: %.2f' % avg_loss)
        
        # Check that we can run the strategy without errors
        # We don't check for specific returns as they depend on market data and strategy behavior
        self.assertIsNotNone(final_value)
        
        # Simple test that verifies the strategy ran
        print(f'Initial Portfolio Value: {initial_value:.2f}')
        print(f'Final Portfolio Value: {final_value:.2f}')
        print(f'Return: {((final_value / initial_value) - 1) * 100:.2f}%')
        
        # If plotting is needed during development:
        # cerebro.plot() # Uncomment for visual inspection

if __name__ == '__main__':
    unittest.main() 