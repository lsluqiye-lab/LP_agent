from decimal import Decimal
from longport.openapi import TradeContext, Config, OrderType, OrderSide, TimeInForceType
import os
from dotenv import load_dotenv

load_dotenv()
ctx = TradeContext(Config.from_env())

try:
    resp = ctx.submit_order(
        symbol="AAPL.US",
        order_type=OrderType.TSMAMT,
        side=OrderSide.Sell,
        submitted_quantity=Decimal("1"),
        time_in_force=TimeInForceType.GoodTilCanceled,
        trailing_amount=Decimal("5.0"),
        remark="Test TSMAMT"
    )
    print("Success TSMAMT:", resp)
except Exception as e:
    print("Error TSMAMT:", e)
