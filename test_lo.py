from decimal import Decimal
from longport.openapi import TradeContext, Config, OrderType, OrderSide, TimeInForceType
import os
from dotenv import load_dotenv

load_dotenv()
ctx = TradeContext(Config.from_env())

try:
    resp = ctx.submit_order(
        symbol="AAPL.US",
        order_type=OrderType.LO,
        side=OrderSide.Sell,
        submitted_quantity=Decimal("1"),
        submitted_price=Decimal("300.0"),
        time_in_force=TimeInForceType.GoodTilCanceled,
        remark="Test LO"
    )
    print("Success LO:", resp)
except Exception as e:
    print("Error LO:", e)
