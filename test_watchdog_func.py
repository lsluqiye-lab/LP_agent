import json
import logging
from unittest.mock import MagicMock, patch

from main import watchdog_check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_watchdog_func")

def test_watchdog_func():
    print("==================================================")
    print("🚀 测试 Watchdog 函数本地执行逻辑 (Mocked)")
    print("==================================================\n")

    # Mock tool registry
    class MockToolRegistry:
        def execute(self, tool_name):
            if tool_name == "get_positions":
                return json.dumps({
                    "positions": [
                        {
                            "symbol": "TSLA",
                            "quantity": "10",
                            "cost_price": "200.00"
                        },
                        {
                            "symbol": "NVDA",
                            "quantity": "5",
                            "cost_price": "100.00"
                        }
                    ]
                })

    # Mock quote ctx
    class MockQuote:
        def __init__(self, last_done, high, low):
            self.last_done = last_done
            self.high = high
            self.low = low

    class MockQuoteCtx:
        def quote(self, symbols):
            if "TSLA" in symbols[0]:
                return [MockQuote("180.00", "205.00", "175.00")] # < 200 * 0.92 = 184 -> DEFENSIVE_DROP
            if "NVDA" in symbols[0]:
                return [MockQuote("120.00", "120.10", "110.00")] # > 100 * 1.15, and near day high -> SWING_EXHAUSTION and/or OFFENSIVE_BREAKOUT
            return [MockQuote("100.00", "100.00", "100.00")]

        def history_candlesticks_by_offset(self, symbol, period, adjust_type, something, count):
            # mock candles for RSI
            class MockCandle:
                def __init__(self, close):
                    self.close = close
            # mock an uptrend for NVDA to get RSI > 80
            if "NVDA" in symbol:
                return [MockCandle(100 + i) for i in range(20)]
            return [MockCandle(100) for i in range(20)]

    mock_tool_registry = MockToolRegistry()

    with patch('tools.market_data.get_quote_ctx', return_value=MockQuoteCtx()):
        with patch('tools.market_data.modify_symbol', side_effect=lambda x: f"{x}.US"):
            events = watchdog_check(mock_tool_registry, logger)
            
            print("\n🚨 发现的中断事件:")
            for e in events:
                print(f"- [{e['symbol']}] {e['type']}: {e['reason']}")

            if not events:
                print("没有发现任何事件。")

if __name__ == "__main__":
    test_watchdog_func()
