from tools.trading import get_longport_config
from longport.openapi import TradeContext, OrderStatus, OrderSide

config = get_longport_config()
trade = TradeContext(config)
orders = trade.today_orders()
for o in orders:
    print(o.symbol, str(o.side), str(o.status))
