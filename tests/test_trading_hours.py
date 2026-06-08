
import unittest
from datetime import datetime, time as dt_time
import pytz
from main import is_trading_hours

class TestTradingHours(unittest.TestCase):
    def test_pre_market_8_30(self):
        """测试盘前 8:30 是否被正确识别为交易监控时段"""
        et = pytz.timezone('US/Eastern')
        # 构造一个周一早上 8:30 的时间点
        test_dt = et.localize(datetime(2026, 6, 8, 8, 30))
        self.assertTrue(is_trading_hours(test_dt), "8:30 ET 应该是盘前防御时段，应返回 True")

    def test_market_10_00(self):
        """测试盘中 10:00 是否正常"""
        et = pytz.timezone('US/Eastern')
        test_dt = et.localize(datetime(2026, 6, 8, 10, 0))
        self.assertTrue(is_trading_hours(test_dt), "10:00 ET 应该是交易时段")

    def test_post_market_16_30(self):
        """测试盘后 16:30 是否在监控范围内"""
        et = pytz.timezone('US/Eastern')
        test_dt = et.localize(datetime(2026, 6, 8, 16, 30))
        self.assertTrue(is_trading_hours(test_dt), "16:30 ET 应该是盘后处理时段")

    def test_midnight_23_00(self):
        """测试深夜 23:00 是否正确休眠"""
        et = pytz.timezone('US/Eastern')
        test_dt = et.localize(datetime(2026, 6, 8, 23, 0))
        self.assertFalse(is_trading_hours(test_dt), "23:00 ET 应该休眠")

    def test_weekend_sunday(self):
        """测试周末是否正确休眠"""
        et = pytz.timezone('US/Eastern')
        # 2026-06-07 是周日
        test_dt = et.localize(datetime(2026, 6, 7, 10, 0))
        self.assertFalse(is_trading_hours(test_dt), "周日应该休眠")

if __name__ == '__main__':
    unittest.main()
