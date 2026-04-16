import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import mplfinance as mpf
import pandas as pd
import os
import json
from datetime import datetime, timedelta

# 显式加载并配置中文字体
font_path = '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc'
if os.path.exists(font_path):
    fm.fontManager.addfont(font_path)
    font_prop = fm.FontProperties(fname=font_path)
    font_name = font_prop.get_name()
else:
    font_name = 'sans-serif' # Fallback

plt.rcParams['font.sans-serif'] = [font_name]
plt.rcParams['axes.unicode_minus'] = False

def plot_trade_signal(ticker: str, trade_type: str, price: float, reason: str = ""):
    """
    绘制交易信号图并保存
    """
    try:
        from tools.market_data import GetKlineTool
        kline_tool = GetKlineTool()
        
        # 获取最近 60 天的数据
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        
        kline_data_str = kline_tool.execute(symbol=ticker, count=90)
        data = json.loads(kline_data_str)
        
        if not data.get("klines"):
            return None
            
        df = pd.DataFrame(data["klines"])
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = df.rename(columns={
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        })
        
        # 转换数值类型
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col])

        # 准备标记点
        # 找到最接近交易时间或最后一根 K 线
        last_idx = df.index[-1]
        
        # 构造标记
        apds = []
        # 可以增加均线
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA50'] = df['Close'].rolling(window=50).mean()
        apds.append(mpf.make_addplot(df['MA20'], color='orange', width=0.7))
        apds.append(mpf.make_addplot(df['MA50'], color='blue', width=0.7))

        # 保存目录
        os.makedirs("data/plots", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"data/plots/{ticker}_{trade_type}_{timestamp}.png"
        
        # 绘图
        # 增加交易点标注
        color = 'red' if trade_type.upper() == 'BUY' else 'green'
        marker = '^' if trade_type.upper() == 'BUY' else 'v'
        
        title = f"{ticker} {trade_type} @ {price}\n{reason[:50]}..."
        
        # 配置中文字体样式
        my_style = mpf.make_mpf_style(base_mpf_style='charles', rc={'font.family': font_name})
        
        mpf.plot(df, type='candle', style=my_style,
                 title=title,
                 ylabel='Price',
                 addplot=apds,
                 volume=True,
                 savefig=filename)
        
        return filename
    except Exception as e:
        print(f"绘图失败: {e}")
        return None
