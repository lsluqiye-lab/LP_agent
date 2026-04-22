from longport.openapi import TradeContext, OrderType
import inspect

print("Submit Order Signature:", inspect.signature(TradeContext.submit_order))
print("OrderType Enum:", [e.name for e in OrderType])
