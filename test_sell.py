from decimal import Decimal
import json
from tools.trading import SellStockTool, BuyStockTool
import os
from dotenv import load_dotenv

load_dotenv()

sell_tool = SellStockTool()
# Test Sell TSMPCT
resp1 = sell_tool.execute(
    symbol="NVDA.US",
    quantity=1,
    order_type="TSMPCT",
    trailing_percent=5.0,
    reason="Test TSMPCT mapping to TSLPPCT"
)
print("Sell TSMPCT:", resp1)

# Test Buy TSMPCT
buy_tool = BuyStockTool()
resp2 = buy_tool.execute(
    symbol="NVDA.US",
    quantity=1,
    order_type="TSMPCT",
    trailing_percent=5.0,
    reason="Test TSMPCT mapping to TSLPPCT"
)
print("Buy TSMPCT:", resp2)

# Cancel orders
from longport.openapi import TradeContext, Config
ctx = TradeContext(Config.from_env())
try:
    data1 = json.loads(resp1)
    if "order_id" in data1:
        ctx.cancel_order(data1["order_id"])
        print("Cancelled Sell order.")
    
    data2 = json.loads(resp2)
    if "order_id" in data2:
        ctx.cancel_order(data2["order_id"])
        print("Cancelled Buy order.")
except Exception as e:
    print("Cleanup error:", e)

