import pytest
from datetime import timedelta, time
import pandas as pd
import pytz
import exchange_calendars
from unittest.mock import patch, MagicMock

from alpaca_backtrader_api.alpacastore import AlpacaStore, Granularity, NY

@pytest.fixture
def store():
    """Fixture to create a mocked AlpacaStore instance for testing"""
    # We need to patch the __init__ method to avoid actual API client creation
    with patch('alpaca_backtrader_api.alpacastore.AlpacaStore.__init__') as mock_init:
        mock_init.return_value = None  # Don't actually run the real __init__
        store = AlpacaStore()
        
        # Manually set up the required attributes that would normally be set in __init__
        store.p = MagicMock()
        store.p.key_id = 'test_key'
        store.p.secret_key = 'test_secret'
        
        # Mock the clients that would be created in __init__
        store.trading_client = MagicMock()
        store.stock_client = MagicMock()
        store.crypto_client = MagicMock()
        store.option_client = MagicMock()
        
        return store

def test_alpaca_broker():
    from alpaca_backtrader_api import alpacabroker
    comminfo = alpacabroker.AlpacaCommInfo(mult=1.0, stocklike=False)
    assert comminfo.getvaluesize(1, 100.00) == 100.00

def test_with_no_dates_specified(store):
    """Test when no begin or end dates are specified."""
    # Both dates None
    result_begin, result_end = store._make_sure_dates_are_initialized_properly(
        None, None, Granularity.Daily
    )
    
    # Assert results are timezone aware and in NY timezone
    assert result_begin.tzinfo.zone == NY
    assert result_end.tzinfo.zone == NY
    
    # End should be close to now, begin should be 30 days before end for daily
    now = pd.Timestamp('now', tz=NY)
    assert (now - result_end).total_seconds() < 10  # Within 10 seconds
    assert abs((result_end - result_begin).days - 30) <= 1

def test_with_minute_granularity(store):
    """Test with minute granularity which requires market open checks."""
    # Mock calendar to simulate market open checks
    with patch('exchange_calendars.get_calendar') as mock_calendar:
        # Setup calendar mock to return market is closed first time, then open
        mock_cal_instance = MagicMock()
        mock_cal_instance.is_open_on_minute.side_effect = [False, True]
        mock_calendar.return_value = mock_cal_instance
        
        result_begin, result_end = store._make_sure_dates_are_initialized_properly(
            None, None, Granularity.Minute
        )
        
        # For minute granularity, begin should be 3 days before end
        assert abs((result_end - result_begin).days - 3) <= 1
        
        # Calendar should have been called to check if market is open
        mock_cal_instance.is_open_on_minute.assert_called()

def test_with_begin_after_end(store):
    """Test when begin date is after end date - should adjust begin date."""
    # Create dates where begin is after end
    ny_tz = pytz.timezone(NY)
    end = pd.Timestamp.now(tz=ny_tz)
    begin = end + pd.Timedelta(days=5)
    
    result_begin, result_end = store._make_sure_dates_are_initialized_properly(
        begin, end, Granularity.Daily
    )
    
    # Result begin should be adjusted to be before end
    assert result_begin <= result_end

def test_timezone_conversion(store):
    """Test that naive datetime objects are properly converted to timezone-aware."""
    # Create naive timestamp objects
    naive_end = pd.Timestamp.now()
    naive_begin = naive_end - pd.Timedelta(days=10)
    
    result_begin, result_end = store._make_sure_dates_are_initialized_properly(
        naive_begin, naive_end, Granularity.Daily
    )
    
    # Results should be timezone aware
    assert result_begin.tzinfo is not None
    assert result_end.tzinfo is not None
    assert result_begin.tzinfo.zone == NY
    assert result_end.tzinfo.zone == NY

def test_already_timezone_aware(store):
    """Test with already timezone-aware datetime objects."""
    # Create timezone-aware timestamp objects in UTC
    utc_end = pd.Timestamp.now(tz='UTC')
    utc_begin = utc_end - pd.Timedelta(days=10)
    
    result_begin, result_end = store._make_sure_dates_are_initialized_properly(
        utc_begin, utc_end, Granularity.Daily
    )
    
    # Results should be converted to NY timezone
    assert result_begin.tzinfo.zone == NY
    assert result_end.tzinfo.zone == NY
    
    # Time difference should remain approximately the same
    original_diff = (utc_end - utc_begin).total_seconds()
    result_diff = (result_end - result_begin).total_seconds()
    assert abs(original_diff - result_diff) <= 10  # Allow small variation due to DST changes

def test_market_hours_adjustment(store):
    """Test that out-of-market-hours are properly adjusted for minute granularity."""
    # Use fixed dates to make test deterministic
    # Create a date with after-hours timestamp (20:00 ET)
    ny_tz = pytz.timezone(NY)
    today = pd.Timestamp.now(tz=ny_tz).replace(hour=20, minute=0, second=0, microsecond=0)
    
    # If today is weekend, move to Friday
    weekday = today.dayofweek
    if weekday >= 5:  # 5=Saturday, 6=Sunday
        today = today - pd.Timedelta(days=(weekday - 4))  # Move to Friday
    
    # Expected result should be the previous day's market close (15:59)
    expected_date = today.date() - pd.Timedelta(days=1)
    
    # Mock the calendar to simulate market closed at 20:00 but open at 16:00 (market close time)
    with patch('exchange_calendars.get_calendar') as mock_calendar:
        # Calendar will say market is closed first time, then open
        mock_cal_instance = MagicMock()
        
        # First call (for original time 20:00) returns False (market closed)
        # Second call (for adjusted time 15:59 previous day) returns True (market open)
        mock_cal_instance.is_open_on_minute.side_effect = [False, True]
        mock_calendar.return_value = mock_cal_instance
        
        result_begin, result_end = store._make_sure_dates_are_initialized_properly(
            None, today, Granularity.Minute
        )
        
        # For minute data with after hours date, it should adjust to 15:59 of the PREVIOUS day
        assert result_end.hour == 15
        assert result_end.minute == 59
        assert result_end.date() == expected_date
        
        # Check calendar was properly called
        assert mock_cal_instance.is_open_on_minute.call_count == 2 