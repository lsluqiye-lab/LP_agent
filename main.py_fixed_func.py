
def is_trading_hours(eastern_time: datetime) -> bool:
    """
    检查是否在交易监控时段（覆盖盘前防御 + 盘中决策 + 盘后复盘）
    8:00 - 17:00 ET
    """
    nyse_holidays = holidays.NYSE()
    today_str = eastern_time.strftime('%Y-%m-%d')
    if nyse_holidays.get(today_str):
        return False
    if eastern_time.weekday() >= 5:
        return False
    current_t = eastern_time.time()
    # 扩大窗口至 8:00 - 17:00 ET，确保盘前黑天鹅防御和盘后对冲对齐
    if dt_time(8, 0) <= current_t <= dt_time(17, 0):
        return True
    return False
