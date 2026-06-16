import sys
import logging
import json
from tools.trading import SellStockTool

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    print("🚀 启动 5月29日 交易漏洞手动修复脚本...")
    tool = SellStockTool()

    print("\n--- [Step 1] 修正 QCOM 追踪止损 ---")
    # QCOM 当前浮盈 +7.63%，最新价格 251.020，成本 233.220
    # 将其 20.5% 的止损修正为更合理的 8.0% 追踪止损，这将在回调触发时锁定约保本或微利状态
    qcom_resp = tool.execute(
        symbol="QCOM",
        quantity=40,
        order_type="TSMPCT",
        trailing_percent=8.0,
        reason="【手动修正】上周五 20.5% 止损过宽，将其修正为 8.0% 追踪止损，锁定保本与盈亏平衡防护。"
    )
    print("QCOM 修正响应:", qcom_resp)

    print("\n--- [Step 2] 修正 FCX 追踪止损 ---")
    # FCX 持仓 188 股，此前仅挂了 170 股
    # 重新全额提交 188 股的 12.0% 追踪止损单（SellStockTool 将自动检测并撤销旧的 170 股 Pending 订单）
    fcx_resp = tool.execute(
        symbol="FCX",
        quantity=188,
        order_type="TSMPCT",
        trailing_percent=12.0,
        reason="【手动修正】补足 5月28日 加仓后的 18 股漏洞，重新全额挂设 188 股 12.0% 追踪止损。"
    )
    print("FCX 修正响应:", fcx_resp)

if __name__ == "__main__":
    main()
