import asyncio
from decimal import Decimal
from longport.openapi import TradeContext, Config, OrderType, OrderSide, TimeInForceType
import os
from dotenv import load_dotenv

load_dotenv()

config = Config.from_env()
ctx = TradeContext(config)

try:
    resp = ctx.submit_order(
        symbol="AAPL.US",
        order_type=OrderType.TSMPCT,
        side=OrderSide.Sell,
        submitted_quantity=Decimal("1"),
        time_in_force=TimeInForceType.Day, # Try Day
        trailing_percent=Decimal("5.0"),
        remark="Test TSMPCT"
    )
    print("Success:", resp)
except Exception as e:
    print("Error (Day):", e)

try:
    resp = ctx.submit_order(
        symbol="AAPL.US",
        order_type=OrderType.TSMPCT,
        side=OrderSide.Sell,
        submitted_quantity=Decimal("1"),
        time_in_force=TimeInForceType.GoodTilCanceled,
        trailing_percent=Decimal("5.0"),
        remark="Test TSMPCT"
    )
    print("Success:", resp)
except Exception as e:
    print("Error (GTC):", e)
