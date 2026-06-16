import json
import glob

files = sorted(glob.glob("data/logs/trade_log_2026-05*.json"))
for f in files:
    try:
        with open(f, 'r') as file:
            data = json.load(file)
            pnl = data.get("daily_pnl", 0)
            trades = data.get("trades", [])
            print(f"{f[-15:-5]}: PnL: {pnl}, Trades: {len(trades)}")
            if len(trades) > 0 and '14' <= f[-7:-5] <= '17':
                 for t in trades:
                     print(f"  - {t.get('action')} {t.get('symbol')} {t.get('quantity')} @ {t.get('price')} | PnL: {t.get('realized_pnl', 'N/A')}")
    except Exception as e:
        print(f"Error reading {f}: {e}")
