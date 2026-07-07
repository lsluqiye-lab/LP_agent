
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

LOG_DIR = Path("data/logs")

def analyze_audits(days=14):
    today = datetime(2026, 7, 7).date()
    start_date = today - timedelta(days=days)
    
    stats = {
        "total_whipsaws": 0,
        "total_over_trading": 0,
        "friction_cost_pct": 0.0,
        "symbol_stats": defaultdict(lambda: {"whipsaws": 0, "over_trading": 0, "friction_sum": 0.0}),
        "event_timeline": []
    }
    
    found_files = 0
    for i in range(days + 1):
        date_str = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        file_path = LOG_DIR / f"trade_log_{date_str}.json"
        
        if file_path.exists():
            found_files += 1
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                    audits = data.get("audit_logs", [])
                    for audit in audits:
                        a_type = audit.get("audit_type")
                        symbol = audit.get("symbol")
                        metrics = audit.get("metrics", {})
                        
                        if a_type == "WHIPSAW":
                            stats["total_whipsaws"] += 1
                            stats["symbol_stats"][symbol]["whipsaws"] += 1
                            friction = metrics.get("friction_pct", 0.0)
                            stats["symbol_stats"][symbol]["friction_sum"] += friction
                            
                        elif a_type == "OVER_TRADING":
                            stats["total_over_trading"] += 1
                            stats["symbol_stats"][symbol]["over_trading"] += 1
                            
            except Exception as e:
                print(f"Error reading {file_path}: {e}")

    # Aggregating
    total_friction = sum(s["friction_sum"] for s in stats["symbol_stats"].values())
    avg_friction = total_friction / stats["total_whipsaws"] if stats["total_whipsaws"] > 0 else 0
    
    print("="*60)
    print(f" LP-Agent 双周审计深度复盘 (过去 {days} 天, 扫描 {found_files} 份日志)")
    print("="*60)
    print(f"【核心指标】")
    print(f"- 扫损回补 (Whipsaw) 总计: {stats['total_whipsaws']} 次")
    print(f"- 过度交易 (Over-Trading) 总计: {stats['total_over_trading']} 次")
    print(f"- 平均单次打脸摩擦成本: {avg_friction:.2f}%")
    print(f"- 累计打脸损耗 (Estimated Friction): {total_friction:.2f}%")
    print("-" * 60)
    
    print(f"【重灾区标的分析 (Symbol Blacklist)】")
    sorted_symbols = sorted(stats["symbol_stats"].items(), key=lambda x: (x[1]["whipsaws"], x[1]["friction_sum"]), reverse=True)
    
    print(f"{'Symbol':<10} | {'Whipsaws':<10} | {'Over-Trade':<10} | {'Total Friction':<15}")
    print("-" * 55)
    for sym, s_data in sorted_symbols[:8]:
        print(f"{sym:<10} | {s_data['whipsaws']:<10} | {s_data['over_trading']:<10} | {s_data['friction_sum']:>6.2f}%")
        
    print("-" * 60)
    print("【诊断结论】")
    if stats["total_whipsaws"] > 5:
        print("⚠️ 扫损回补频繁：当前 ATR 追踪止损可能过窄，导致系统在主升浪的回撤中被洗出。")
    if stats["total_over_trading"] > 20:
        print("⚠️ 交易频率过高：决策信息噪音较大，导致止损单频繁重设，增加了潜在的 API 延迟与执行风险。")
    print("="*60)

if __name__ == "__main__":
    analyze_audits()
