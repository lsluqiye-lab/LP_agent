from longport.openapi import Config, QuoteContext, SubType, PushQuote, TradeContext, OrderStatus, OrderSide, Market, \
    OrderType, TimeInForceType
import os
from datetime import datetime, timedelta, date, time
import json
from http import HTTPStatus
from dashscope import Application
from decimal import Decimal
import holidays
import pytz
import time as ttime


class stockInfo:
    def __init__(self, code, amount, currency, quantity):
        self.symbol = code
        self.amount = amount
        self.currency = currency
        self.quantity = quantity

    def to_dict(self):
        return {
            'symbol': self.symbol,
            'amount': self.amount,
            'currency': self.currency,
            'quantity': self.quantity
        }


class order:
    def __init__(self, code, side, amount, currency, quantity, status, time):
        self.symbol = code
        self.side = side
        self.amount = amount
        self.currency = currency
        self.quantity = quantity
        self.status = status
        self.tradeTime = time

    def to_dict(self):
        return {
            'symbol': self.symbol,
            'side': self.side,
            'amount': self.amount,
            'currency': self.currency,
            'quantity': self.quantity,
            'status': self.status,
            'tradeTime': self.tradeTime
        }

LP_KEY = {
    "LONGPORT_APP_KEY": "",
    "LONGPORT_APP_SECRET": "",
    "LONGPORT_ACCESS_TOKEN": ""
}

for k, v in LP_KEY.items():
    if k not in os.environ:
        os.environ[k] = v
    else:
        print(f'当前的{k}为{v}')

def buildPrompt():
    try:
        config = Config.from_env()
        trade = TradeContext(config)
        prompt = '现在是美东时间'+ str(datetime.now(pytz.timezone('US/Eastern')))
        if (market_is_open(datetime.now(pytz.timezone('US/Eastern')))):
            prompt = prompt + ',当前市场状态为:盘中\n'
        elif (market_is_pre(datetime.now(pytz.timezone('US/Eastern')))):
            prompt = prompt + ',当前市场状态为:盘前\n'
        elif (market_is_after(datetime.now(pytz.timezone('US/Eastern')))):
            prompt = prompt + ',当前市场状态为:盘后\n'
        else:
            prompt = prompt + ',当前市场状态为:夜盘\n'
        position = trade.stock_positions().channels
        positions = []
        for p in position:
            for stock in p.positions:
                if ('USD' == stock.currency):
                    positions.append(
                        stockInfo(cut_symbol(stock.symbol), str(stock.cost_price), stock.currency, str(stock.available_quantity)))
        # 关键：这里必须将每个 stockInfo 对象转换为字典
        positions_dict = [p.to_dict() for p in positions]
        prompt = prompt + '当前的持仓情况为:' + json.dumps(positions_dict) + '.\n'

        balance = trade.account_balance('USD')
        cash = ''
        netAssets = ''
        for b in balance:
            netAssets = str(b.net_assets)
            cash = str(b.total_cash)
        prompt = prompt + '当前账户的总资产为:' + netAssets + ' USD,当前账户的可用现金为:' + cash + ' USD.\n'

        historyOrder = trade.history_orders(
            start_at=datetime.now() - timedelta(days=7),
            end_at=datetime.now() + timedelta(days=1)
        )
        historyInfo = []
        for o in historyOrder:
            if ('USD' == o.currency):
                historyInfo.append(
                    order(cut_symbol(o.symbol), str(o.side), str(o.executed_price), o.currency, str(o.executed_quantity),
                          str(o.status), str(o.submitted_at.astimezone(pytz.timezone('US/Eastern')))))
        historyInfo = convertHistoryInfo(historyInfo, 2, 10)
        history_dict = [h.to_dict() for h in historyInfo]
        prompt = prompt + '过去一周执行的委托情况如下:' + json.dumps(history_dict) + '.\n'

        todayOrder = trade.today_orders()
        todayInfo = []
        for t in todayOrder:
            if ('USD' == t.currency):
                todayInfo.append(
                    order(cut_symbol(t.symbol), str(t.side), str(t.executed_price), t.currency, str(t.executed_quantity),
                          str(t.status), str(t.submitted_at.astimezone(pytz.timezone('US/Eastern')))))
        today_dict = [t.to_dict() for t in todayInfo]
        prompt = prompt + '今天执行的委托情况如下:' + json.dumps(today_dict) + '.'

        return prompt
    except Exception as e:
        return e

def convertHistoryInfo(historyInfo, n, m):
    convertHistory = []
    dateSet = set()
    for history in historyInfo:
        dateSet.add(history.tradeTime[:10])
    dateList = list(dateSet)
    dateList.sort(reverse=True)
    index = 0
    for history in historyInfo:
        if (history.tradeTime[:10] in dateList[:n]):
            index = index + 1
            convertHistory.append(history)
        if (index>=m):
            break
    return convertHistory


def invokeAgent(prompt):
    try:
        response = Application.call(
            # 若没有配置环境变量，可用百炼API Key将下行替换为：api_key="sk-xxx"。但不建议在生产环境中直接将API Key硬编码到代码中，以减少API Key泄露风险。
            api_key='sk-cec82c52f0264a45a2c0e2c8cbb2928a',
            app_id='ed3d9da3bbd2457db8c2604d70685b59',  # 替换为实际的应用 ID deepseek
            prompt=prompt)

        if response.status_code != HTTPStatus.OK:
            print(f'request_id={response.request_id}')
            print(f'code={response.status_code}')
            print(f'message={response.message}')
            print(f'请参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/error-code')
        else:
            return response.output.text
    except Exception as e:
        return e


def buyMOStock(code, quantity, remark):
    config = Config.from_env()
    trade = TradeContext(config)
    trade.submit_order(
        side=OrderSide.Buy,
        symbol=code,
        order_type=OrderType.MO,
        submitted_quantity=Decimal(quantity),
        time_in_force=TimeInForceType.Day,
        remark=remark
    )

def buyLOStock(code, quantity, price, remark):
    config = Config.from_env()
    trade = TradeContext(config)
    trade.submit_order(
        side=OrderSide.Buy,
        symbol=code,
        order_type=OrderType.LO,
        submitted_quantity=Decimal(quantity),
        submitted_price=Decimal(price),
        time_in_force=TimeInForceType.Day,
        remark=remark
    )


def sellMOStock(code, quantity, remark):
    config = Config.from_env()
    trade = TradeContext(config)
    trade.submit_order(
        side=OrderSide.Sell,
        symbol=code,
        order_type=OrderType.MO,
        submitted_quantity=Decimal(quantity),
        time_in_force=TimeInForceType.Day,
        remark=remark
    )

def sellLOStock(code, quantity, price, remark):
    config = Config.from_env()
    trade = TradeContext(config)
    trade.submit_order(
        side=OrderSide.Sell,
        symbol=code,
        order_type=OrderType.LO,
        submitted_quantity=Decimal(quantity),
        submitted_price=Decimal(price),
        time_in_force=TimeInForceType.Day,
        remark=remark
    )


def LOSellCheck(symbol, price):
    config = Config.from_env()
    quote = QuoteContext(config)
    queryResult = quote.quote([symbol])
    for query in queryResult:
        if (price < query.last_done):
            return False
    return True

def LOBuyCheck(symbol, price):
    config = Config.from_env()
    quote = QuoteContext(config)
    queryResult = quote.quote([symbol])
    for query in queryResult:
        if (price > query.last_done):
            return False
    return True

def handleResponse(response):
    try:
        commandInfo = json.loads(response)
        for command in commandInfo:
            if ('buy' == command['action']):
                if ('LO'==command['orderType']):
                    buyLOStock(modify_symbol(command['symbol']), command['quantity'], float(command['price']),
                         f"{command['reason']}{command['confidence']}")
                else:
                    buyMOStock(modify_symbol(command['symbol']), command['quantity'],
                               f"{command['reason']}{command['confidence']}")

            if ('sell' == command['action']):
                if ('LO'==command['orderType']):
                    sellLOStock(modify_symbol(command['symbol']), command['quantity'], float(command['price']),
                               f"{command['reason']}{command['confidence']}")
                else:
                    sellMOStock(modify_symbol(command['symbol']), command['quantity'],
                                f"{command['reason']}{command['confidence']}")

        return 'Handle Success'
    except Exception as e:
        return e


def market_is_open(current_time_eastern):
    """
    Checks whether the U.S. stock market (NYSE) is currently open based on Eastern Standard Time (EST).
    This function considers both the current date and time, taking into account:
    - U.S. holidays observed by NYSE.
    - Weekends (Saturday and Sunday) when the market is closed.
    - Market open hours (9:00 AM to 4:00 PM EST) on weekdays when the market is open.
    """

    try:

        nyse_holidays = holidays.NYSE()
        today_date_str = current_time_eastern.strftime('%Y-%m-%d')
        is_holiday = nyse_holidays.get(today_date_str)
        if is_holiday:
            return False

        # Check if the current day (EST) is a weekday
        day_of_week = current_time_eastern.weekday()
        if day_of_week == 5 or day_of_week == 6:
            return False

        # Define the market open and close times in EST
        market_open_time = time(9, 30)  # Market opens at 9:00 AM EST
        market_close_time = time(16, 0)  # Market closes at 4:00 PM EST

        if market_open_time <= current_time_eastern.time() <= market_close_time:
            return True
        else:
            return False

    except Exception as err:
        print(err)

    return True

def market_is_after(current_time_eastern):
    """
    Checks whether the U.S. stock market (NYSE) is currently open based on Eastern Standard Time (EST).
    This function considers both the current date and time, taking into account:
    - U.S. holidays observed by NYSE.
    - Weekends (Saturday and Sunday) when the market is closed.
    - Market open hours (9:00 AM to 4:00 PM EST) on weekdays when the market is open.
    """

    try:

        nyse_holidays = holidays.NYSE()
        today_date_str = current_time_eastern.strftime('%Y-%m-%d')
        is_holiday = nyse_holidays.get(today_date_str)
        if is_holiday:
            return False

        # Check if the current day (EST) is a weekday
        day_of_week = current_time_eastern.weekday()
        if day_of_week == 5 or day_of_week == 6:
            return False

        # Define the market open and close times in EST
        market_open_time = time(16, 0)  # Market opens at 9:00 AM EST
        market_close_time = time(20, 0)  # Market closes at 4:00 PM EST

        if market_open_time <= current_time_eastern.time() <= market_close_time:
            return True
        else:
            return False

    except Exception as err:
        print(err)

    return True

def market_is_pre(current_time_eastern):
    """
    Checks whether the U.S. stock market (NYSE) is currently open based on Eastern Standard Time (EST).
    This function considers both the current date and time, taking into account:
    - U.S. holidays observed by NYSE.
    - Weekends (Saturday and Sunday) when the market is closed.
    - Market open hours (9:00 AM to 4:00 PM EST) on weekdays when the market is open.
    """

    try:

        nyse_holidays = holidays.NYSE()
        today_date_str = current_time_eastern.strftime('%Y-%m-%d')
        is_holiday = nyse_holidays.get(today_date_str)
        if is_holiday:
            return False

        # Check if the current day (EST) is a weekday
        day_of_week = current_time_eastern.weekday()
        if day_of_week == 5 or day_of_week == 6:
            return False

        # Define the market open and close times in EST
        market_open_time = time(4, 0)  # Market opens at 9:00 AM EST
        market_close_time = time(9, 30)  # Market closes at 4:00 PM EST

        if market_open_time <= current_time_eastern.time() <= market_close_time:
            return True
        else:
            return False

    except Exception as err:
        print(err)

    return True


def obtainSleepTime():
    if (market_is_open(datetime.now(pytz.timezone('US/Eastern')))):
        return int(300)
    if (market_is_open(datetime.now(pytz.timezone('US/Eastern')) + timedelta(hours=1))):
        return int(300)
    return int(1800)

def cut_symbol(symbol):
    if (check_symbol(symbol)):
        return symbol[:-3]
    return symbol

def modify_symbol(symbol):
    if (check_symbol(symbol)):
        return symbol
    return symbol + '.US'


def check_symbol(symbol):
    return len(symbol) >= 3 and symbol[-3:] == '.US'

while (True):
    logName = "log-" + str(date.today()) + ".txt"
    print("***===***")
    print('Start at', datetime.now())
    if (True != (market_is_pre(datetime.now(pytz.timezone('US/Eastern'))) 
    or market_is_open(datetime.now(pytz.timezone('US/Eastern'))) 
    or market_is_after(datetime.now(pytz.timezone('US/Eastern'))))):
        print("当前属于夜盘，市场尚未开启。")
        print('Finish at', datetime.now(), 'Sleep ', str(obtainSleepTime()), ' seconds')
        print("***===***")
        ttime.sleep(obtainSleepTime())
        continue
    prompt = buildPrompt()
    print(prompt)
    respone = invokeAgent(prompt)
    print(respone)
    handle = handleResponse(respone)
    print(str(handle))
    print('Finish at', datetime.now(), 'Sleep ', str(obtainSleepTime()), ' seconds')
    print("***===***")
    ttime.sleep(obtainSleepTime())
