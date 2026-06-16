import time
from longport.openapi import TradeContext, Config
config = Config.from_env()
ctx = TradeContext(config)
orders = ctx.today_orders()
count = 0
for o in orders:
    status_str = str(o.status).lower()
    if any(s in status_str for s in ["notreported", "new", "submitted", "pending", "partialfilled", "varietiesnotreported"]):
        try:
            ctx.cancel_order(o.order_id)
            print(f"Canceled {o.symbol} order {o.order_id}")
            count += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"Failed to cancel {o.symbol}: {e}")
            time.sleep(1)
print(f"Total canceled: {count}")
