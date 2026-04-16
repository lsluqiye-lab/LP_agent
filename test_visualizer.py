import asyncio
from tools.visualizer import plot_trade_signal

def test_visualizer():
    print("=== 测试绘图功能 ===")
    # 模拟为 NVDA 绘制一个买入信号
    path = plot_trade_signal("NVDA", "BUY", 900.0, "测试交易：价格突破前高，MA20 支撑强劲。")
    if path:
        print(f"绘图成功: {path}")
    else:
        print("绘图失败")

if __name__ == "__main__":
    test_visualizer()
