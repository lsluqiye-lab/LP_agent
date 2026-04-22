import json
from tools.trading import get_longport_config
from longport.openapi import QuoteContext
try:
    config = get_longport_config()
    quote = QuoteContext(config)
    info = quote.static_info(["NVDA", "ASML", "AAPL"])
    for i in info:
        print(f"Symbol: {i.symbol}")
        print(f"Industry: {getattr(i, 'industry', 'N/A')}")
        print(f"Sector: {getattr(i, 'sector', 'N/A')}")
        print(f"Dir: {dir(i)}")
        print("---")
except Exception as e:
    print("Error:", e)
