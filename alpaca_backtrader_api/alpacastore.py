from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import os
import collections
import pytz
from enum import Enum
import traceback

from datetime import datetime, timedelta, time as dtime
import uuid
from dateutil.parser import parse as date_parse
import time as _time
import exchange_calendars
import threading
import asyncio

from alpaca.data.timeframe import TimeFrame
from alpaca.data.live.websocket import DataStream
from alpaca.data.live.stock import StockDataStream
from alpaca.data.live.option import OptionDataStream
from alpaca.data.live.crypto import CryptoDataStream
from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.trading.requests import LimitOrderRequest, StopOrderRequest, StopLimitOrderRequest, TrailingStopOrderRequest, MarketOrderRequest
from alpaca.trading.models import Asset
from alpaca.trading.models import Order
from alpaca.trading.enums import AssetClass, TimeInForce, OrderSide, OrderType, OrderClass
from alpaca.trading.stream import TradingStream
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest, OptionBarsRequest
from alpaca.common.exceptions import APIError
import pytz
import pandas as pd

import backtrader as bt
from backtrader.metabase import MetaParams
from backtrader.utils.py3 import queue, with_metaclass

import logging
logger = logging.getLogger(__name__)

NY = 'America/New_York'


# Extend the exceptions to support extra cases
class AlpacaError(APIError):
    """ Generic error class, catches Alpaca response errors
    """

    def __init__(self, error_response):
        self.error_response = error_response
        msg = "Alpaca API returned error code %s (%s) " % \
              (error_response['code'], error_response['message'])

        super(AlpacaError, self).__init__(msg)


class AlpacaRequestError(AlpacaError):
    def __init__(self):
        er = dict(code=599, message='Request Error', description='')
        super(self.__class__, self).__init__(er)


class AlpacaStreamError(AlpacaError):
    def __init__(self, content=''):
        er = dict(code=598, message='Failed Streaming', description=content)
        super(self.__class__, self).__init__(er)


class AlpacaTimeFrameError(AlpacaError):
    def __init__(self, content):
        er = dict(code=597, message='Not supported TimeFrame', description='')
        super(self.__class__, self).__init__(er)


class AlpacaNetworkError(AlpacaError):
    def __init__(self):
        er = dict(code=596, message='Network Error', description='')
        super(self.__class__, self).__init__(er)


class Granularity(Enum):
    Ticks = "ticks"
    Daily = "day"
    Minute = "minute"


class StreamingMethod(Enum):
    AccountUpdate = 'account_update'
    Quote = "quote"
    MinuteAgg = "minute_agg"


class Streamer:
    conn: DataStream | TradingStream = None

    def __init__(
            self,
            q,
            instrument: Asset = None,
            api_key='',
            api_secret='',
            method: StreamingMethod = StreamingMethod.AccountUpdate,
            data_feed=DataFeed.IEX,
            *args,
            **kwargs):
        try:
            # make sure we have an event loop, if not create a new one
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        if method == StreamingMethod.AccountUpdate:
            self.conn = TradingStream(api_key=api_key, secret_key=api_secret)
        else:
            if instrument.asset_class == AssetClass.US_EQUITY:
                self.conn = StockDataStream(api_key=api_key,
                                    secret_key=api_secret,
                                    feed=data_feed)
            elif instrument.asset_class == AssetClass.US_OPTION:
                self.conn = OptionDataStream(api_key=api_key,
                                    secret_key=api_secret,
                                    feed=data_feed)
            elif instrument.asset_class == AssetClass.CRYPTO:
                self.conn = CryptoDataStream(api_key=api_key,
                                    secret_key=api_secret)
            else:
                raise ValueError(f"Unsupported asset class: {instrument.asset_class}")
        self.instrument = instrument
        self.method = method
        self.q = q

    def run(self):
        if self.method == StreamingMethod.AccountUpdate:
            self.conn.subscribe_trade_updates(self.on_trade)
        elif self.method == StreamingMethod.MinuteAgg:
            self.conn.subscribe_bars(self.on_agg_min, self.instrument.symbol)
        elif self.method == StreamingMethod.Quote:
            self.conn.subscribe_quotes(self.on_quotes, self.instrument.symbol)

        # this code runs in a new thread. we need to set the loop for it
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.conn.run()

    async def on_listen(self, conn, stream, msg):
        pass

    async def on_quotes(self, msg):
        # For Quote objects, we need to handle bid/ask prices that _load_tick expects
        quote_dict = {
            # Convert datetime object to pandas Timestamp to match expected format
            'time': pd.Timestamp(msg.timestamp),
            'bid_price': msg.bid_price if hasattr(msg, 'bid_price') else msg.close,
            'ask_price': msg.ask_price if hasattr(msg, 'ask_price') else msg.close,
            'volume': msg.volume if hasattr(msg, 'volume') else 0
        }
        self.q.put(quote_dict)

    async def on_agg_min(self, msg):
        # Convert Bar object to a dictionary compatible with alpacadata
        logger.debug(f"Streamer received minute aggregate: {msg}")
        bar_dict = {
            # Convert datetime object to pandas Timestamp to match expected format
            'time': pd.Timestamp(msg.timestamp),
            'open': msg.open,
            'high': msg.high,
            'low': msg.low,
            'close': msg.close,
            'volume': msg.volume
        }
        self.q.put(bar_dict)

    async def on_account(self, msg):
        self.q.put(msg)

    async def on_trade(self, msg):
        try:
            # Extract the order details from the message
            # The trade update has order as a direct attribute
            if hasattr(msg, 'order'):
                order: Order = msg.order
                order_dict = {
                    'id': str(order.id) if isinstance(order.id, uuid.UUID) else order.id,
                    'status': order.status.value,
                    'filled_qty': order.filled_qty,
                    'filled_avg_price': order.filled_avg_price,
                    'side': order.side.value
                }
                self.q.put(order_dict)
            else:
                # Fallback for other message formats
                self.q.put(msg)
        except Exception as e:
            print(f"Error processing trade update: {e}")
            # Pass the original message as a fallback
            self.q.put(msg)


class MetaSingleton(MetaParams):
    '''Metaclass to make a metaclassed class a singleton'''

    def __init__(cls, name, bases, dct):
        super(MetaSingleton, cls).__init__(name, bases, dct)
        cls._singleton = None

    def __call__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = (
                super(MetaSingleton, cls).__call__(*args, **kwargs))

        return cls._singleton


class AlpacaStore(with_metaclass(MetaSingleton, object)):
    '''Singleton class wrapping to control the connections to Alpaca.

    Params:

      - ``key_id`` (default:``None``): Alpaca API key id

      - ``secret_key`` (default: ``None``): Alpaca API secret key

      - ``paper`` (default: ``False``): use the paper trading environment

      - ``account_tmout`` (default: ``10.0``): refresh period for account
        value/cash refresh
    '''

    BrokerCls = None  # broker class will autoregister
    DataCls = None  # data class will auto register

    params = (
        ('key_id', ''),
        ('secret_key', ''),
        ('paper', False),
        ('account_tmout', 10.0),  # account balance refresh timeout
        ('api_version', None)
    )

    _DTEPOCH = datetime(1970, 1, 1)
    _ENVPRACTICE = 'paper'
    _ENVLIVE = 'live'
    _ENV_PRACTICE_URL = 'https://paper-api.alpaca.markets'
    _ENV_LIVE_URL = 'https://api.alpaca.markets'

    @classmethod
    def getdata(cls, *args, **kwargs):
        '''Returns ``DataCls`` with args, kwargs'''
        return cls.DataCls(*args, **kwargs)

    @classmethod
    def getbroker(cls, *args, **kwargs):
        '''Returns broker with *args, **kwargs from registered ``BrokerCls``'''
        return cls.BrokerCls(*args, **kwargs)

    def __init__(self):
        super(AlpacaStore, self).__init__()

        self.notifs = collections.deque()  # store notifications for cerebro

        self._env = None  # reference to cerebro for general notifications
        self.broker = None  # broker instance
        self.datas = list()  # datas that have registered over start

        self._orders = collections.OrderedDict()  # map order.ref to oid
        self._ordersrev = collections.OrderedDict()  # map oid to order.ref
        self._transpend = collections.defaultdict(collections.deque)

        if self.p.paper:
            self._oenv = self._ENVPRACTICE
            self.p.base_url = self._ENV_PRACTICE_URL
        else:
            self._oenv = self._ENVLIVE
            self.p.base_url = self._ENV_LIVE_URL
        
        self.trading_client = TradingClient(api_key=self.p.key_id, secret_key=self.p.secret_key)
        self.stock_client = StockHistoricalDataClient(api_key=self.p.key_id, secret_key=self.p.secret_key)
        self.crypto_client = CryptoHistoricalDataClient(api_key=self.p.key_id, secret_key=self.p.secret_key)
        self.option_client = OptionHistoricalDataClient(api_key=self.p.key_id, secret_key=self.p.secret_key)

        self._cash = 0.0
        self._value = 0.0
        self._evt_acct = threading.Event()

    def start(self, data=None, broker=None):
        # Datas require some processing to kickstart data reception
        if data is None and broker is None:
            self.cash = None
            return

        if data is not None:
            self._env = data._env
            # For datas simulate a queue with None to kickstart co
            self.datas.append(data)

            if self.broker is not None:
                self.broker.data_started(data)

        elif broker is not None:
            self.broker = broker
            self.streaming_events()
            self.broker_threads()

    def stop(self):
        # signal end of thread
        if self.broker is not None:
            self.q_ordercreate.put(None)
            self.q_orderclose.put(None)
            self.q_account.put(None)

    def put_notification(self, msg, *args, **kwargs):
        self.notifs.append((msg, args, kwargs))

    def get_notifications(self):
        '''Return the pending "store" notifications'''
        self.notifs.append(None)  # put a mark / threads could still append
        return [x for x in iter(self.notifs.popleft, None)]

    # Alpaca supported granularities
    _GRANULARITIES = {
        (bt.TimeFrame.Minutes, 1):  '1Min',
        (bt.TimeFrame.Minutes, 5):  '5Min',
        (bt.TimeFrame.Minutes, 15): '15Min',
        (bt.TimeFrame.Minutes, 60): '1H',
        (bt.TimeFrame.Days, 1):     '1D',
    }

    def get_positions(self):
        try:
            positions = self.trading_client.get_all_positions()
        except (AlpacaError, AlpacaRequestError,):
            return []
        return positions

    def get_granularity(self, timeframe, compression) -> Granularity:
        if timeframe == bt.TimeFrame.Ticks:
            return Granularity.Ticks
        if timeframe == bt.TimeFrame.Minutes:
            return Granularity.Minute
        elif timeframe == bt.TimeFrame.Days:
            return Granularity.Daily

    def get_instrument(self, dataname):
        try:
            insts = self.trading_client.get_asset(dataname)
        except (AlpacaError, AlpacaRequestError,):
            return None

        return insts or None

    def streaming_events(self, tmout=None):
        q = queue.Queue()
        kwargs = {'q': q, 'tmout': tmout}

        t = threading.Thread(target=self._t_streaming_listener, kwargs=kwargs)
        t.daemon = True
        t.start()

        t = threading.Thread(target=self._t_streaming_events, kwargs=kwargs)
        t.daemon = True
        t.start()
        return q

    def _t_streaming_listener(self, q, tmout=None):
        while True:
            trans = q.get()
            # Check if trans is already in the expected dictionary format
            # or if it has the order attribute (old format)
            if isinstance(trans, dict):
                self._transaction(trans)
            elif hasattr(trans, 'order'):
                self._transaction(trans.order)
            else:
                # Try to handle anyway
                try:
                    self._transaction(trans)
                except Exception as e:
                    print(f"Error processing transaction: {e}")

    def _t_streaming_events(self, q, tmout=None):
        if tmout is not None:
            _time.sleep(tmout)
        streamer = Streamer(q,
                            api_key=self.p.key_id,
                            api_secret=self.p.secret_key,
                            )

        streamer.run()

    def candles(self, dataname, dtbegin, dtend, timeframe, compression,
                candleFormat, includeFirst):
        """

        :param dataname: symbol name. e.g AAPL
        :param dtbegin: datetime start
        :param dtend: datetime end
        :param timeframe: bt.TimeFrame
        :param compression: distance between samples. e.g if 1 =>
                 get sample every day. if 3 => get sample every 3 days
        :param candleFormat: (bidask, midpoint, trades) - not used we get bars
        :param includeFirst:
        :return:
        """

        kwargs = locals().copy()
        kwargs.pop('self')
        kwargs['q'] = q = queue.Queue()
        t = threading.Thread(target=self._t_candles, kwargs=kwargs)
        t.daemon = True
        t.start()
        return q

    @staticmethod
    def iso_date(date_str):
        """
        this method will make sure that dates are formatted properly
        as with isoformat
        :param date_str:
        :return: YYYY-MM-DD date formatted
        """
        return date_parse(date_str).date().isoformat()

    def _t_candles(self, dataname, dtbegin, dtend, timeframe, compression,
                   candleFormat, includeFirst, q):
        granularity: Granularity = self.get_granularity(timeframe, compression)
        dtbegin, dtend = self._make_sure_dates_are_initialized_properly(
            dtbegin, dtend, granularity)

        if granularity is None:
            e = AlpacaTimeFrameError('granularity is missing')
            q.put(e.error_response)
            return
        try:
            cdl = self.get_aggs_from_alpaca(dataname,
                                            dtbegin,
                                            dtend,
                                            granularity,
                                            compression)
        except AlpacaError as e:
            print(str(e))
            q.put(e.error_response)
            q.put(None)
            return
        except Exception:
            traceback.print_exc()
            q.put({'code': 'error'})
            q.put(None)
            return

        # don't use dt.replace. use localize
        # (https://stackoverflow.com/a/1592837/2739124)
        cdl = cdl.loc[
              pytz.timezone(NY).localize(dtbegin) if
              not dtbegin.tzname() else dtbegin:
              pytz.timezone(NY).localize(dtend) if
              not dtend.tzname() else dtend
              ].dropna(subset=['high'])
        records = cdl.reset_index().to_dict('records')
        for r in records:
            r['time'] = r['timestamp']
            q.put(r)
        q.put({})  # end of transmission

    def _make_sure_dates_are_initialized_properly(self, dtbegin: pd.Timestamp | None, dtend: pd.Timestamp | None,
                                                  granularity: Granularity):
        """
        dates may or may not be specified by the user.
        when they do, they are probably don't include NY timezome data
        also, when granularity is minute, we want to make sure we get data when
        market is opened. so if it doesn't - let's set end date to be last
        known minute with opened market.
        this nethod takes care of all these issues.
        :param dtbegin:
        :param dtend:
        :param granularity:
        :return:
        """
        if not dtend:
            dtend = pd.Timestamp('now', tz=NY)
        else:
            dtend = pd.Timestamp(pytz.timezone('UTC').localize(dtend)) if \
              not dtend.tzname() else dtend
        if granularity == Granularity.Minute:
            calendar = exchange_calendars.get_calendar(name='NYSE')
            while not calendar.is_open_on_minute(dtend.ceil(freq='min')):
                dtend = dtend.replace(hour=15,
                                      minute=59,
                                      second=0,
                                      microsecond=0)
                dtend -= timedelta(days=1)
        if not dtbegin:
            days = 30 if granularity == Granularity.Daily else 3
            delta = timedelta(days=days)
            dtbegin = dtend - delta
        else:
            dtbegin = pd.Timestamp(pytz.timezone('UTC').localize(dtbegin)) if \
              not dtbegin.tzname() else dtbegin
        while dtbegin > dtend:
            # if we start the script during market hours we could get this
            # situation. this resolves that.
            dtbegin -= timedelta(days=1)
        return dtbegin.astimezone(pytz.timezone(NY)), dtend.astimezone(pytz.timezone(NY))

    def get_aggs_from_alpaca(self,
                             dataname,
                             start,
                             end,
                             granularity: Granularity,
                             compression):
        """
        https://alpaca.markets/docs/api-documentation/api-v2/market-data/bars/
        Alpaca API as a limit of 1000 records per api call. meaning, we need to
        do multiple calls to get all the required data if the date range is
        large.
        also, the alpaca api does not support compression (or, you can't get
        5 minute bars e.g) so we need to resample the received bars.
        also, we need to drop out of market records.
        this function does all of that.
        """

        def _granularity_to_timeframe(granularity):
            if granularity in [Granularity.Minute, Granularity.Ticks]:
                timeframe = TimeFrame.Minute
            elif granularity == Granularity.Daily:
                timeframe = TimeFrame.Day
            elif granularity == 'ticks':
                timeframe = "minute"
            else:
                # default to day if not configured properly. subject to
                # change.
                timeframe = TimeFrame.Day
            return timeframe

        def _iterate_api_calls():
            """
            you could get max 1000 samples from the server. if we need more
            than that we need to do several api calls.

            currently the alpaca api supports also 5Min and 15Min so we could
            optimize server communication time by addressing timeframes
            """
            got_all = False
            curr = end
            response = pd.DataFrame()
            while not got_all:
                timeframe = _granularity_to_timeframe(granularity)
                asset = self.get_instrument(dataname)
                if asset.asset_class == AssetClass.US_EQUITY:
                    r = self.stock_client.get_stock_bars(
                        StockBarsRequest(
                            symbol_or_symbols=dataname,
                            timeframe=timeframe,
                            start=start.isoformat(),
                            end=curr.isoformat()
                        )
                    )
                elif asset.asset_class == AssetClass.CRYPTO:
                    r = self.crypto_client.get_crypto_bars(
                        CryptoBarsRequest(
                            symbol_or_symbols=dataname,
                            timeframe=timeframe,
                            start=start.isoformat(),
                            end=curr.isoformat()
                        )
                    )
                elif asset.asset_class == AssetClass.US_OPTION:
                    r = self.option_client.get_option_bars(
                        OptionBarsRequest(
                            symbol_or_symbols=dataname,
                            timeframe=timeframe,
                            start=start.isoformat(),
                            end=curr.isoformat()
                        )
                    )
                if r and dataname in r.data and len(r.data[dataname]) > 0:
                    # BarSet contains a dict with symbol as key and List[Bar] as value
                    bars = r.data[dataname]
                    # Extract the earliest timestamp from the first bar
                    earliest_sample = bars[0].timestamp
                    
                    # Convert the bars to a DataFrame
                    bar_dicts = [{
                        'open': bar.open,
                        'high': bar.high,
                        'low': bar.low,
                        'close': bar.close,
                        'volume': bar.volume,
                        'timestamp': bar.timestamp
                    } for bar in bars]
                    
                    bar_df = pd.DataFrame(bar_dicts)
                    # Set timestamp as index
                    bar_df.set_index('timestamp', inplace=True)
                    
                    response = pd.concat([bar_df, response], axis=0)
                    
                    if earliest_sample <= (pytz.timezone(NY).localize(
                            start) if not start.tzname() else start):
                        got_all = True
                    else:
                        delta = timedelta(days=1) if granularity == "day" \
                            else timedelta(minutes=1)
                        curr = earliest_sample - delta
                else:
                    # no more data is available, let's return what we have
                    break
            return response

        def _clear_out_of_market_hours(df):
            """
            only interested in samples between 9:30, 16:00 NY time
            """
            return df.between_time("09:30", "16:00")

        def _drop_early_samples(df):
            """
            samples from server don't start at 9:30 NY time
            let's drop earliest samples
            """
            for i, b in df.iterrows():
                if i.time() >= dtime(9, 30):
                    return df[i:]

        def _resample(df):
            """
            samples returned with certain window size (1 day, 1 minute) user
            may want to work with different window size (5min)
            """

            if granularity == Granularity.Minute:
                sample_size = f"{compression}Min"
            else:
                sample_size = f"{compression}D"
            df = df.resample(sample_size).agg(
                collections.OrderedDict([
                    ('open', 'first'),
                    ('high', 'max'),
                    ('low', 'min'),
                    ('close', 'last'),
                    ('volume', 'sum'),
                ])
            )
            if granularity == Granularity.Minute:
                return df.between_time("09:30", "16:00")
            else:
                return df

        if not start:
            timeframe = _granularity_to_timeframe(granularity)
            start = end - timedelta(days=1)
            asset = self.get_instrument(dataname)
            if asset.asset_class == AssetClass.US_EQUITY:
                r = self.stock_client.get_stock_bars(
                    StockBarsRequest(
                        symbol_or_symbols=dataname,
                        timeframe=timeframe,
                        start=start.isoformat(),
                        end=end.isoformat()
                    )
                )
            elif asset.asset_class == AssetClass.CRYPTO:
                r = self.crypto_client.get_crypto_bars(
                    CryptoBarsRequest(
                        symbol_or_symbols=dataname,
                        timeframe=timeframe,
                        start=start.isoformat(),
                        end=end.isoformat()
                    )
                )
            elif asset.asset_class == AssetClass.US_OPTION:
                r = self.option_client.get_option_bars(
                    OptionBarsRequest(
                        symbol_or_symbols=dataname,
                        timeframe=timeframe,
                        start=start.isoformat(),
                        end=end.isoformat()
                    )
                )
            # Convert BarSet to DataFrame
            if r and dataname in r.data and len(r.data[dataname]) > 0:
                bars = r.data[dataname]
                bar_dicts = [{
                    'open': bar.open,
                    'high': bar.high,
                    'low': bar.low,
                    'close': bar.close,
                    'volume': bar.volume,
                    'timestamp': bar.timestamp
                } for bar in bars]
                
                response = pd.DataFrame(bar_dicts)
                response.set_index('timestamp', inplace=True)
            else:
                response = pd.DataFrame()
        else:
            response = _iterate_api_calls()
        cdl = response
        if granularity == Granularity.Minute:
            cdl = _clear_out_of_market_hours(cdl)
            cdl = _drop_early_samples(cdl)
        if compression != 1:
            response = _resample(cdl)
        else:
            response = cdl
        response = response.dropna()
        response = response[~response.index.duplicated()]
        return response

    def streaming_prices(self,
                         dataname, timeframe, tmout=None, data_feed=DataFeed.IEX):
        logger.debug(f"Starting streaming prices for {dataname} with timeframe {timeframe}")
        q = queue.Queue()
        kwargs = {'q':         q,
                  'dataname':  dataname,
                  'timeframe': timeframe,
                  'data_feed': data_feed,
                  'tmout':     tmout}
        t = threading.Thread(target=self._t_streaming_prices, kwargs=kwargs)
        t.daemon = True
        t.start()
        return q

    def _t_streaming_prices(self, dataname, timeframe, q, tmout, data_feed):
        if tmout is not None:
            _time.sleep(tmout)

        if timeframe == bt.TimeFrame.Ticks:
            method = StreamingMethod.Quote
        elif timeframe == bt.TimeFrame.Minutes:
            method = StreamingMethod.MinuteAgg
        else:
            method = StreamingMethod.MinuteAgg

        streamer = Streamer(q,
                            instrument=self.get_instrument(dataname),
                            api_key=self.p.key_id,
                            api_secret=self.p.secret_key,
                            method=method,
                            data_feed=data_feed)

        streamer.run()

    def get_cash(self):
        return self._cash

    def get_value(self):
        return self._value

    _ORDEREXECS = {
        bt.Order.Market:    OrderType.MARKET,
        bt.Order.Limit:     OrderType.LIMIT,
        bt.Order.Stop:      OrderType.STOP,
        bt.Order.StopLimit: OrderType.STOP_LIMIT,
        bt.Order.StopTrail: OrderType.TRAILING_STOP,
    }

    def broker_threads(self):
        self.q_account = queue.Queue()
        self.q_account.put(True)  # force an immediate update
        t = threading.Thread(target=self._t_account)
        t.daemon = True
        t.start()

        self.q_ordercreate = queue.Queue()
        t = threading.Thread(target=self._t_order_create)
        t.daemon = True
        t.start()

        self.q_orderclose = queue.Queue()
        t = threading.Thread(target=self._t_order_cancel)
        t.daemon = True
        t.start()

        # Wait once for the values to be set
        self._evt_acct.wait(self.p.account_tmout)

    def _t_account(self):
        while True:
            try:
                msg = self.q_account.get(timeout=self.p.account_tmout)
                if msg is None:
                    break  # end of thread
            except queue.Empty:  # tmout -> time to refresh
                pass

            try:
                accinfo = self.trading_client.get_account()
            except Exception as e:
                self.put_notification(e)
                continue

            try:
                self._cash = float(accinfo.cash)
                self._value = float(accinfo.portfolio_value)
            except KeyError:
                pass

            self._evt_acct.set()

    def order_create(self, order: bt.order.Order, stopside=None, takeside=None, **kwargs):
        okwargs = dict()
        symbol = order.data._name if order.data._name else order.data._dataname
        okwargs['symbol'] = symbol
        
        qty = abs(float(order.created.size))
        qty = round(qty, 2)
        okwargs['qty'] = qty
            
        okwargs['side'] = OrderSide.BUY if order.isbuy() else OrderSide.SELL
        okwargs['type'] = self._ORDEREXECS[order.exectype]
        
        # Get asset class first to determine correct time_in_force
        asset = self.trading_client.get_asset(symbol)
        # For Crypto Trading, Alpaca only supports gtc, and ioc (https://alpaca.markets/docs/api-references/trading-api/orders/#time-in-force)
        okwargs['time_in_force'] = TimeInForce.GTC if asset.asset_class == AssetClass.CRYPTO else TimeInForce.DAY
        
        # Handle different order types and their prices
        if order.exectype == bt.Order.Market:
            # Market orders don't need a price
            logger.debug(f"Creating market order for {symbol} - Side: {okwargs['side']}, Size: {okwargs['qty']}")
        elif order.exectype == bt.Order.Limit:
            if order.price is not None:
                okwargs['limit_price'] = order.price
                logger.debug(f"Creating limit order for {symbol} - Side: {okwargs['side']}, Size: {okwargs['qty']}, Price: {okwargs['limit_price']}")
            else:
                logger.error(f"Limit order for {symbol} has no price!")
                raise ValueError("Limit orders require a price")
        elif order.exectype in [bt.Order.StopLimit, bt.Order.Stop]:
            if order.price is not None:
                okwargs['stop_price'] = order.price
                if order.exectype == bt.Order.StopLimit:
                    if order.created.pricelimit is not None:
                        okwargs['limit_price'] = order.created.pricelimit
                    else:
                        logger.error(f"StopLimit order for {symbol} has no limit price!")
                        raise ValueError("StopLimit orders require a limit price")
                logger.debug(f"Creating stop order for {symbol} - Side: {okwargs['side']}, Size: {okwargs['qty']}, Stop: {okwargs['stop_price']}")
            else:
                logger.error(f"Stop order for {symbol} has no price!")
                raise ValueError("Stop orders require a price")
        elif order.exectype == bt.Order.StopTrail:
            if order.trailpercent and order.trailamount:
                raise ValueError("You can't create trailing stop order with both TrailPrice and TrailPercent. choose one")
            if order.trailpercent:
                okwargs['trail_percent'] = order.trailpercent
            elif order.trailamount:
                okwargs['trail_price'] = order.trailamount
            else:
                raise ValueError("You must provide either trailpercent or trailamount when creating StopTrail order")
            logger.debug(f"Creating trailing stop order for {symbol} - Side: {okwargs['side']}, Size: {okwargs['qty']}")

        if stopside:
            okwargs['stop_loss'] = {'stop_price': str(stopside.price)}
            logger.debug(f"Adding stop loss at {stopside.price}")

        if takeside:
            okwargs['take_profit'] = {'limit_price': str(takeside.price)}
            logger.debug(f"Adding take profit at {takeside.price}")

        if stopside or takeside:
            okwargs['order_class'] = "bracket"
            logger.debug("Creating bracket order")

        # anything from the user
        okwargs.update(order.info)
        okwargs.update(**kwargs)

        logger.debug(f"Final order parameters: {okwargs}")
        self.q_ordercreate.put((order.ref, okwargs,))
        return order

    def _t_order_create(self):
        def _check_if_transaction_occurred(order_id):
            # a transaction may have happened and was stored. if so let's
            # process it
            tpending = self._transpend[order_id]
            tpending.append(None)  # eom marker
            while True:
                trans = tpending.popleft()
                if trans is None:
                    break
                self._process_transaction(order_id, trans)

        while True:
            try:
                if self.q_ordercreate.empty():
                    continue
                msg = self.q_ordercreate.get()
                if msg is None:
                    continue
                oref, okwargs = msg
                try:
                    logger.debug(f"Submitting order {oref} to Alpaca: {okwargs}")
                    
                    # Initialize order request based on order type
                    basic_order_data = {
                        "symbol": okwargs.get('symbol'),
                        "qty": okwargs.get('qty'),
                        "side": okwargs.get('side'),
                        "time_in_force": okwargs.get('time_in_force')
                    }
                    order_type = okwargs.get('type')

                    if order_type == OrderType.LIMIT:
                        order_request = LimitOrderRequest(
                            **basic_order_data,
                            limit_price=okwargs.get('limit_price')
                        )
                    elif order_type == OrderType.STOP:
                        order_request = StopOrderRequest(
                            **basic_order_data,
                            stop_price=okwargs.get('stop_price')
                        )
                    elif order_type == OrderType.STOP_LIMIT:
                        order_request = StopLimitOrderRequest(
                            **basic_order_data,
                            stop_price=okwargs.get('stop_price'),
                            limit_price=okwargs.get('limit_price')
                        )
                    elif order_type == OrderType.TRAILING_STOP:
                        order_request = TrailingStopOrderRequest(
                            **basic_order_data,
                        )
                        if 'trail_percent' in okwargs:
                            order_request.trail_percent = okwargs.get('trail_percent')
                        elif 'trail_price' in okwargs:
                            order_request.trail_price = okwargs.get('trail_price')
                    else:
                        order_request = MarketOrderRequest(**basic_order_data)
                    
                    # Add bracket order parameters if present
                    if okwargs.get('order_class') == 'bracket':
                        order_request.order_class = OrderClass.BRACKET
                        if 'stop_loss' in okwargs:
                            order_request.stop_loss = okwargs.get('stop_loss')
                        if 'take_profit' in okwargs:
                            order_request.take_profit = okwargs.get('take_profit')

                    logger.debug(f"Final order parameters: {order_request}")
                    o = self.trading_client.submit_order(order_data=order_request)
                    logger.debug(f"Order {oref} submitted successfully: {o}")
                except Exception as e:
                    logger.error(f"Error submitting order {oref}: {str(e)}")
                    self.put_notification(e)
                    self.broker._reject(oref)
                    continue
                try:
                    oid = o.id
                except Exception:
                    if 'code' in o._raw:
                        self.put_notification(f"error submitting order "
                                              f"code: {o.code}. msg: "
                                              f"{o.message}")
                    else:
                        self.put_notification(
                            "General error from the Alpaca server")
                    self.broker._reject(oref)
                    continue

                if okwargs['type'] == 'market':
                    self.broker._accept(oref)  # taken immediately

                self._orders[oref] = oid
                self._ordersrev[oid] = oref  # maps ids to backtrader order
                _check_if_transaction_occurred(oid)
                if o.legs:
                    index = 1
                    for leg in o.legs:
                        self._orders[oref + index] = leg.id
                        self._ordersrev[leg.id] = oref + index
                        _check_if_transaction_occurred(leg.id)
                        index += 1
                self.broker._submit(oref)  # inside it submits the legs too
                if okwargs['type'] == 'market':
                    self.broker._accept(oref)  # taken immediately

            except Exception as e:
                print(str(e))

    def order_cancel(self, order):
        self.q_orderclose.put(order.ref)
        return order

    def _t_order_cancel(self):
        while True:
            oref = self.q_orderclose.get()
            if oref is None:
                break

            oid = self._orders.get(oref, None)
            if oid is None:
                continue  # the order is no longer there
            try:
                self.trading_client.cancel_order(order_id=oid)
            except Exception as e:
                self.put_notification(
                    "Order not cancelled: {}, {}".format(
                        oid, e))
                continue

            self.broker._cancel(oref)

    _X_ORDER_CREATE = (
        'new',
        'accepted',
        'pending_new',
        'accepted_for_bidding',
    )

    def _transaction(self, trans):
        # Invoked from Streaming Events. May actually receive an event for an
        # oid which has not yet been returned after creating an order. Hence
        # store if not yet seen, else forward to processer
        try:
            oid = trans['id']

            if not self._ordersrev.get(oid, False):
                self._transpend[oid].append(trans)
            else:
                self._process_transaction(oid, trans)
        except Exception as e:
            print(f"ERROR processing transaction: {e}")
            traceback.print_exc()

    _X_ORDER_FILLED = ('partially_filled', 'filled',)

    def _process_transaction(self, oid, trans):
        try:
            oref = self._ordersrev.pop(oid)
        except KeyError:
            return

        ttype = trans['status']

        if ttype in self._X_ORDER_FILLED:
            size = float(trans['filled_qty'])
            if trans['side'] == 'sell':
                size = -size
            price = float(trans['filled_avg_price'])
            self.broker._fill(oref, size, price, ttype=ttype)

        elif ttype in self._X_ORDER_CREATE:
            self.broker._accept(oref)
            self._ordersrev[oid] = oref

        elif ttype == 'calculated':
            return

        elif ttype == 'expired':
            self.broker._expire(oref)
        else:  # default action ... if nothing else
            print("Process transaction - Order type: {}".format(ttype))
            self.broker._reject(oref)
