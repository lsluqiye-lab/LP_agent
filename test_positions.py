from tools.trading import get_longport_config
from longport.openapi import TradeContext
try:
    config = get_longport_config()
    trade = TradeContext(config)
    resp = trade.stock_positions()
    print("stock_positions() channels:", dir(resp))
    if hasattr(resp, 'channels'):
        for c in resp.channels:
            print("Channel dir:", dir(c))
            for p in c.positions:
                print("Position:", dir(p), p.symbol, p.quantity)
                break
            break
except Exception as e:
    print("Error:", e)
