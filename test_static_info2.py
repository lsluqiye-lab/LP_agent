import json
from tools.trading import get_longport_config
from longport.openapi import QuoteContext
try:
    config = get_longport_config()
    quote = QuoteContext(config)
    info = quote.static_info(["NVDA.US", "ASML.US", "AAPL.US"])
    for i in info:
        print(f"Symbol: {i.symbol}")
        print(f"Dir: {dir(i)}")
        print(f"NameCN: {i.name_cn}, NameEN: {i.name_en}")
        print("---")
except Exception as e:
    print("Error:", e)
