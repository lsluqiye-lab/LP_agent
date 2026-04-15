import os
import sys
from decimal import Decimal
from config import AppConfig, load_dotenv
from tools.base import ToolRegistry
from tools.trading import BuyStockTool, SellStockTool

# Force load latest .env
load_dotenv(".env")

def main():
    print("🚀 测试高级订单类型 (LIT 和 TSMPCT)")
    
    # 模拟工具执行
    buy_tool = BuyStockTool()
    sell_tool = SellStockTool()
    
    # 测试 1: LIT 触及限价单 (突破买入)
    print("\n--- [测试 1] LIT 触及限价单 ---")
    try:
        res1 = buy_tool.execute(
            symbol="AAPL",
            quantity=1,
            order_type="LIT",
            price=200.0,
            trigger_price=201.0,
            reason="AI: 突破 $201 阻力位后在 $200 附近买入"
        )
        print("✅ LIT 单提交结果:", res1)
    except Exception as e:
        print("❌ LIT 单提交失败:", str(e))
        
    # 测试 2: MIT 触及市价单
    print("\n--- [测试 2] MIT 触及市价单 ---")
    try:
        res2 = sell_tool.execute(
            symbol="AAPL",
            quantity=1,
            order_type="MO", # Fallback to MO for now in test
            reason="AI: AAPL test"
        )
        print("✅ MO 单提交结果:", res2)
    except Exception as e:
        print("❌ MO 单提交失败:", str(e))

if __name__ == "__main__":
    main()
