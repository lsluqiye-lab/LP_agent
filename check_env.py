from longport.openapi import Config, TradeContext
import os

try:
    config = Config.from_env()
    ctx = TradeContext(config)
    
    # 获取账户余额
    balances = ctx.account_balance('USD')
    print("-" * 30)
    print("LongPort Connection Test:")
    for b in balances:
        print(f"Net Assets: {b.net_assets}")
        print(f"Total Cash: {b.total_cash}")
        print(f"Currency: {b.currency}")
    
    # 尝试判断环境 (某些SDK版本可以通过 ctx 获取)
    # 这里的关键是看输出的资产数额是否符合你的实盘账户
    print("-" * 30)
    print("SUCCESS: Connected to LongPort")
except Exception as e:
    print(f"FAILED: {str(e)}")
