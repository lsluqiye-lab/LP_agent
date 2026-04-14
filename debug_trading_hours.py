
import pytz
import holidays
from datetime import datetime, time as dt_time

def is_trading_hours(eastern_time: datetime) -> bool:
    nyse_holidays = holidays.NYSE()
    today_str = eastern_time.strftime('%Y-%m-%d')
    holiday_name = nyse_holidays.get(today_str)
    if holiday_name:
        print(f"DEBUG: Today is a holiday: {holiday_name}")
        return False
    if eastern_time.weekday() >= 5:
        print(f"DEBUG: Today is weekend: {eastern_time.weekday()}")
        return False
    current_t = eastern_time.time()
    print(f"DEBUG: Current ET time: {current_t}")
    if dt_time(9, 30) <= current_t <= dt_time(16, 0):
        return True
    return False

eastern = pytz.timezone('US/Eastern')
now = datetime.now(eastern)
print(f"Is trading hours: {is_trading_hours(now)}")
