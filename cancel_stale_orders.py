import time
from datetime import datetime, timezone
import pytz
from tools.trading import get_longport_config
from longport.openapi import TradeContext, OrderStatus

def cancel_stale_orders():
    print("============================================================")
    print("  LP-Agent 自动清理历史过期/陈旧挂单工具")
    print("============================================================")
    
    config = get_longport_config()
    ctx = TradeContext(config)
    orders = ctx.today_orders()
    
    # 设定基准时间：2026-05-28 00:00:00 美东时间
    # 任何在此时间之前提交的挂单均视为陈旧挂单
    eastern = pytz.timezone('US/Eastern')
    cutoff_date = datetime(2026, 5, 28, 0, 0, 0, tzinfo=eastern)
    
    count = 0
    for o in orders:
        status_str = str(o.status).lower()
        # 判定是否属于未成交的挂单状态
        is_pending = any(s in status_str for s in ["notreported", "new", "submitted", "pending", "partialfilled", "varietiesnotreported"])
        
        # 获取提交时间
        submitted_at = o.submitted_at.astimezone(eastern)
        is_stale = submitted_at < cutoff_date
        
        print(f"检查订单: {o.symbol:<8} | 方向: {str(o.side):<15} | 状态: {str(o.status):<35} | 提交时间: {submitted_at} | 挂单中?: {is_pending:<5} | 是否陈旧?: {is_stale}")
        
        if is_pending and is_stale:
            try:
                print(f"--> [发现陈旧挂单] 正在撤销 {o.symbol} 挂单 (ID: {o.order_id}, 数量: {o.quantity}, 提交时间: {submitted_at})...")
                ctx.cancel_order(o.order_id)
                print(f"✅ 成功撤销 {o.symbol} 订单 {o.order_id}")
                count += 1
                time.sleep(0.5)
            except Exception as e:
                print(f"❌ 撤销 {o.symbol} 订单失败: {e}")
                time.sleep(1)
                
    print("============================================================")
    print(f"清理完成! 共成功撤销了 {count} 个陈旧挂单。")
    print("============================================================")

if __name__ == "__main__":
    cancel_stale_orders()
