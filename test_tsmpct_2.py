import asyncio
from decimal import Decimal
from longport.openapi import TradeContext, Config, OrderType, OrderSide, TimeInForceType
import os
from dotenv import load_dotenv

load_dotenv()
ctx = TradeContext(Config.from_env())

def test_trailing(pct_val):
    try:
        resp = ctx.submit_order(
            symbol="AAPL.US",
            order_type=OrderType.TSMPCT,
            side=OrderSide.Sell,
            submitted_quantity=Decimal("1"),
            time_in_force=TimeInForceType.GoodTilCanceled,
            trailing_percent=Decimal(str(pct_val)),
            remark="Test TSMPCT"
        )
        print(f"Success {pct_val}:", resp)
    except Exception as e:
        print(f"Error {pct_val}:", e)

test_trailing("0.05")
test_trailing("5")
test_trailing("5.0")
test_trailing("5.00")
