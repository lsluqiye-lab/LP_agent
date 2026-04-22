from decimal import Decimal
from longport.openapi import TradeContext, Config, OrderType, OrderSide, TimeInForceType, OutsideRTH
import os
from dotenv import load_dotenv

load_dotenv()
ctx = TradeContext(Config.from_env())

try:
    resp = ctx.submit_order(
        symbol="TSLA.US",
        order_type=OrderType.TSMPCT,
        side=OrderSide.Sell,
        submitted_quantity=Decimal("1"),
        time_in_force=TimeInForceType.Day,
        trailing_percent=Decimal("5.0"),
        outside_rth=OutsideRTH.RTHOnly
    )
    print("Success:", resp)
except Exception as e:
    print("Error:", e)
