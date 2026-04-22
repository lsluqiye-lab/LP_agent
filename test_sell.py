from tools.trading import SellStockTool
import asyncio

tool = SellStockTool()
res = tool.execute(symbol="QQQ", quantity=14, order_type="MO")
print(res)
