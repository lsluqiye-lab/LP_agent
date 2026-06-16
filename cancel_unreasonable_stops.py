import time
from longport.openapi import TradeContext, Config

def cut_symbol(symbol: str) -> str:
    if '.' in symbol:
        return symbol.split('.')[0]
    return symbol

def cancel_stops():
    config = Config.from_env()
    ctx = TradeContext(config)
    orders = ctx.today_orders()
    count = 0
    symbols_to_cancel = ["TSLA", "AMZN", "UNH"]
    for o in orders:
        status_str = str(o.status).lower()
        # 匹配 pending 状态的订单
        is_pending = any(s in status_str for s in ["notreported", "new", "submitted", "pending", "partialfilled", "varietiesnotreported"])
        # 校验是否为目标证券，且为卖单
        short_symbol = cut_symbol(o.symbol)
        is_target = short_symbol in symbols_to_cancel
        is_sell = "sell" in str(o.side).lower()
        
        print(f"Checking order: {o.symbol} ({short_symbol}), Side: {o.side}, Status: {o.status}, Pending?: {is_pending}, Target?: {is_target}")
        
        if is_pending and is_target and is_sell:
            try:
                ctx.cancel_order(o.order_id)
                print(f"--> Successfully Canceled {o.symbol} order {o.order_id} (Side: {o.side}, Status: {o.status})")
                count += 1
                time.sleep(0.5)
            except Exception as e:
                print(f"--> Failed to cancel {o.symbol} order {o.order_id}: {e}")
                time.sleep(1)
    print(f"Total target orders canceled: {count}")

if __name__ == "__main__":
    cancel_stops()
