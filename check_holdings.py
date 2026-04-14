
import json
import os
from tools.trading import GetPositionsTool, GetAccountBalanceTool, get_longport_config
from longport.openapi import TradeContext

def check_connection():
    print("--- 正在检查 LongPort 连接 ---")
    try:
        config = get_longport_config()
        # 尝试初始化 TradeContext 来验证 token
        trade = TradeContext(config)
        print("✅ LongPort 配置加载成功")
        
        # 1. 获取账户余额
        balance_tool = GetAccountBalanceTool()
        balance_raw = balance_tool.execute()
        balance = json.loads(balance_raw)
        
        print("\n--- 账户余额 (USD) ---")
        if "error" in balance:
            print(f"❌ 获取余额失败: {balance['error']}")
        else:
            print(f"净资产: ${float(balance.get('net_assets', 0)):,.2f}")
            print(f"可用现金: ${float(balance.get('total_cash', 0)):,.2f}")

        # 2. 获取持仓信息
        pos_tool = GetPositionsTool()
        pos_raw = pos_tool.execute()
        pos_data = json.loads(pos_raw)
        
        print("\n--- 当前持仓 ---")
        if "error" in pos_data:
            print(f"❌ 获取持仓失败: {pos_data['error']}")
        else:
            positions = pos_data.get("positions", [])
            print(f"总计: {pos_data.get('count', 0)} 个标的")
            print("-" * 50)
            print(f"{'代码':<10} | {'数量':<10} | {'成本价':<10} | {'市值'}")
            print("-" * 50)
            for p in positions:
                print(f"{p['symbol']:<10} | {p['quantity']:<10} | {p['cost_price']:<10} | {p['market_value']}")
                
    except Exception as e:
        print(f"❌ 运行出错: {e}")

if __name__ == "__main__":
    check_connection()
