from decimal import Decimal
from longport.openapi import TradeContext, Config, OrderType, OrderSide, TimeInForceType
import os
from dotenv import load_dotenv

load_dotenv()
ctx = TradeContext(Config.from_env())

try:
    resp = ctx.submit_order(
        symbol="AAPL.US",
        order_type=OrderType.TSMPCT,
        side=OrderSide.Sell,
        submitted_quantity=Decimal("1"),
        time_in_force=TimeInForceType.Day,
        trailing_percent=Decimal("5.0"),
        outside_rth=False,  # let's just omit outside_rth first. Wait, outside_rth=False
    )
    print("Success:", resp)
except Exception as e:
    print("Error:", e)
