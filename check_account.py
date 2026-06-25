import asyncio
from tools.engines.factory import get_trading_engine

async def main():
    engine = get_trading_engine()
    balance = engine.get_account_balance()
    print("Account Balance:", balance)
    
    positions = engine.get_positions()
    print("\nPositions:", positions)
    
    orders = engine.get_today_orders()
    print("\nToday's Orders (Pending/Recent):")
    for order in orders:
        print(f"ID: {order.get('order_id')}, Symbol: {order.get('symbol')}, Side: {order.get('side')}, Status: {order.get('status')}, Price: {order.get('price')}, Trigger: {order.get('trigger_price')}, Trailing: {order.get('trailing_percent')}")

if __name__ == "__main__":
    asyncio.run(main())
