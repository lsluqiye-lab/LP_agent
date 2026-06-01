"""
交易工具集
封装LongPort OpenAPI的交易功能
"""
import json
import logging
from datetime import datetime, timedelta, time
from decimal import Decimal
from typing import Optional

import pytz
import holidays
from longport.openapi import (
    Config, QuoteContext, TradeContext,
    OrderSide, OrderType, TimeInForceType, OutsideRTH
)

from tools.base import BaseTool, ToolParameter
from data.trade_logger import get_trade_logger



def _calculate_dynamic_slippage(symbol, base_price, order_side, order_type):
    """
    计算动态滑点和容错率。避免整数关口交易拥挤，提高成交率。
    """
    from tools.market_data import get_quote_ctx, modify_symbol
    try:
        quote_ctx = get_quote_ctx()
        quotes = quote_ctx.quote([modify_symbol(symbol)])
        current_price = float(quotes[0].last_done) if quotes else float(base_price)
    except:
        current_price = float(base_price)
    
    if current_price > 500:
        slippage_pct = 0.001
    elif current_price > 50:
        slippage_pct = 0.002
    else:
        slippage_pct = 0.003
        
    slippage_amt = current_price * slippage_pct
    slippage_amt = max(slippage_amt, 0.02)
    # 对于限价单和追踪止损单的容错偏移 (limit_offset)，为了防范剧烈波动时的击穿，给予更宽的容忍度
    limit_offset = max(slippage_amt * 8, 0.2)
    
    adjusted_price = float(base_price)
    if order_side == "Buy":
        if order_type == "LO":
            adjusted_price = float(base_price) + slippage_amt
        elif order_type == "LIT":
            adjusted_price = float(base_price) + slippage_amt * 1.5
    else:
        if order_type == "LO":
            adjusted_price = float(base_price) - slippage_amt
        elif order_type == "LIT":
            adjusted_price = float(base_price) - slippage_amt * 1.5
            
    return round(adjusted_price, 2), round(limit_offset, 2)


def _has_duplicate_pending_order(trade_ctx, symbol, order_side):
    """检查是否已有同向的未成交挂单，防止系统无限重复下发条件单"""
    try:
        orders = trade_ctx.today_orders()
        clean_symbol = symbol.split('.')[0]
        side_str = "Buy" if order_side == "Buy" else "Sell"
        
        for o in orders:
            # 匹配标的和方向
            if o.symbol.startswith(clean_symbol) and side_str in str(o.side):
                status_str = str(o.status).lower()
                # 检查挂单状态 (未报、待报、已报、部分成交等均属于挂单中)
                if any(s in status_str for s in ["notreported", "new", "submitted", "pending", "partialfilled"]):
                    return True
    except Exception as e:
        logging.warning(f"检查挂单状态时发生异常: {e}")
    return False


def _cancel_duplicate_pending_orders(trade_ctx, symbol, order_side) -> int:
    """
    自动检索并撤销同向的所有未成交挂单，防止新单下达时产生冲突，返回成功撤销的订单数量。
    """
    cancelled_count = 0
    try:
        orders = trade_ctx.today_orders()
        clean_symbol = symbol.split('.')[0]
        side_str = "Buy" if order_side == "Buy" else "Sell"

        for o in orders:
            # 匹配标的和方向
            if o.symbol.startswith(clean_symbol) and side_str in str(o.side):
                status_str = str(o.status).lower()
                # 检查挂单状态 (未报、待报、已报、部分成交等均属于挂单中)
                if any(s in status_str for s in ["notreported", "new", "submitted", "pending", "partialfilled"]):
                    logging.info(f"🚨 [Cancel-Before-Modify] 发现同向冲突未成交订单 {o.order_id}，正在自动秒级下达撤单指令...")
                    trade_ctx.cancel_order(o.order_id)
                    cancelled_count += 1
        if cancelled_count > 0:
            import time
            logging.info(f"⏳ [Cancel-Before-Modify] 已成功发送 {cancelled_count} 个冲突订单的撤单指令，短暂休眠 0.5s 等对冲额度释放...")
            time.sleep(0.5)
    except Exception as e:
        logging.warning(f"[Cancel-Before-Modify] 自动撤销同向挂单时发生异常: {e}")
    return cancelled_count


def get_longport_config() -> Config:
    """获取LongPort配置"""
    return Config.from_env()


def cut_symbol(symbol: str) -> str:
    """移除股票代码后缀"""
    if len(symbol) >= 3 and symbol[-3:] == '.US':
        return symbol[:-3]
    return symbol


def _format_order_status_for_llm(status) -> str:
    """
    将 LongPort OpenAPI 晦涩的订单状态映射或附加清晰的英文和状态说明，
    方便 LLM 准确理解挂单是否处于 PENDING / FILLED / CANCELED 状态。
    """
    status_str = str(status)
    if "VarietiesNotReported" in status_str:
        return f"{status_str} (PENDING_CONDITIONAL)"  # 待触发条件单，仍有效挂单中
    elif "NotReported" in status_str:
        return f"{status_str} (PENDING_SUBMITTED)"    # 待报单，仍有效挂单中
    elif "New" in status_str:
        return f"{status_str} (NEW)"
    elif "Filled" in status_str and "Partial" not in status_str:
        return f"{status_str} (FILLED)"
    elif "Canceled" in status_str:
        return f"{status_str} (CANCELED)"
    elif "Rejected" in status_str:
        return f"{status_str} (REJECTED)"
    elif "Expired" in status_str:
        return f"{status_str} (EXPIRED)"
    elif "PendingCancel" in status_str:
        return f"{status_str} (PENDING_CANCEL)"
    return status_str


def modify_symbol(symbol: str) -> str:
    """添加股票代码后缀"""
    if len(symbol) >= 3 and symbol[-3:] == '.US':
        return symbol
    return symbol + '.US'


class GetMarketStatusTool(BaseTool):
    """获取市场状态工具"""

    name = "get_market_status"
    description = "获取当前美股市场状态（盘前、盘中、盘后、休市）"
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            eastern = pytz.timezone('US/Eastern')
            current_time = datetime.now(eastern)

            # 检查是否是假日
            nyse_holidays = holidays.NYSE()
            today_str = current_time.strftime('%Y-%m-%d')
            if nyse_holidays.get(today_str):
                return json.dumps({
                    "status": "closed",
                    "reason": "holiday",
                    "current_time": str(current_time),
                    "message": f"今天是美国假日: {nyse_holidays.get(today_str)}"
                })

            # 检查是否是周末
            day_of_week = current_time.weekday()
            if day_of_week >= 5:
                return json.dumps({
                    "status": "closed",
                    "reason": "weekend",
                    "current_time": str(current_time),
                    "message": "周末休市"
                })

            current_t = current_time.time()

            # 盘前: 4:00 - 9:30
            if time(4, 0) <= current_t < time(9, 30):
                return json.dumps({
                    "status": "pre_market",
                    "current_time": str(current_time),
                    "message": "当前为盘前交易时段"
                })

            # 盘中: 9:30 - 16:00
            if time(9, 30) <= current_t <= time(16, 0):
                return json.dumps({
                    "status": "open",
                    "current_time": str(current_time),
                    "message": "当前为正常交易时段"
                })

            # 盘后: 16:00 - 20:00
            if time(16, 0) < current_t <= time(20, 0):
                return json.dumps({
                    "status": "after_hours",
                    "current_time": str(current_time),
                    "message": "当前为盘后交易时段"
                })

            # 夜盘/休市
            return json.dumps({
                "status": "closed",
                "reason": "outside_trading_hours",
                "current_time": str(current_time),
                "message": "当前为非交易时段"
            })

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class GetPositionsTool(BaseTool):
    """获取持仓工具"""

    name = "get_positions"
    description = "获取当前账户的股票持仓信息，包括股票代码、持仓数量、成本价、现价以及浮盈百分比"
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)
            quote = QuoteContext(config)

            position_resp = trade.stock_positions()
            positions = []
            symbols = []

            # 收集所有股票代码
            for channel in position_resp.channels:
                for stock in channel.positions:
                    if stock.currency == 'USD':
                        symbols.append(stock.symbol)

            # 批量获取报价
            quotes_map = {}
            if symbols:
                quote_res = quote.quote(symbols)
                for q in quote_res:
                    quotes_map[q.symbol] = q.last_done

            for channel in position_resp.channels:
                for stock in channel.positions:
                    if stock.currency == 'USD':
                        last_done = quotes_map.get(stock.symbol)
                        cost_price = float(stock.cost_price)
                        
                        profit_pct = 0.0
                        if last_done and cost_price > 0:
                            profit_pct = (float(last_done) - cost_price) / cost_price * 100

                        positions.append({
                            "symbol": cut_symbol(stock.symbol),
                            "quantity": str(stock.available_quantity),
                            "cost_price": str(stock.cost_price),
                            "last_done": str(last_done) if last_done else "N/A",
                            "profit_pct": f"{profit_pct:.2f}%",
                            "currency": stock.currency,
                            "market_value": str(stock.market_value) if hasattr(stock, 'market_value') else "N/A"
                        })

            return json.dumps({
                "positions": positions,
                "count": len(positions)
            })

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class GetAccountBalanceTool(BaseTool):
    """获取账户余额工具"""

    name = "get_account_balance"
    description = "获取账户余额信息，包括总资产、可用现金等"
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)

            balances = trade.account_balance('USD')

            result = {}
            for b in balances:
                result = {
                    "net_assets": str(b.net_assets),
                    "total_cash": str(b.total_cash),
                    "currency": "USD"
                }
                break

            return json.dumps(result)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class GetTodayOrdersTool(BaseTool):
    """获取今日订单工具"""

    name = "get_today_orders"
    description = "获取今天的订单记录"
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)
            eastern = pytz.timezone('US/Eastern')

            orders = trade.today_orders()
            order_list = []

            for o in orders:
                if o.currency == 'USD':
                    order_list.append({
                        "symbol": cut_symbol(o.symbol),
                        "side": str(o.side),
                        "status": _format_order_status_for_llm(o.status),
                        "quantity": str(o.quantity),
                        "executed_quantity": str(o.executed_quantity),
                        "price": str(o.price) if o.price else "市价",
                        "executed_price": str(o.executed_price) if o.executed_price else "N/A",
                        "submitted_at": str(o.submitted_at.astimezone(eastern))
                    })

            return json.dumps({
                "orders": order_list,
                "count": len(order_list)
            })

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class GetHistoryOrdersTool(BaseTool):
    """获取历史订单工具"""

    name = "get_history_orders"
    description = "获取过去一段时间的历史订单记录"
    parameters = [
        ToolParameter(
            name="days",
            type="integer",
            description="查询天数，默认7天",
            required=False,
            default=7
        )
    ]

    def execute(self, days: int = 7, **kwargs) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)
            eastern = pytz.timezone('US/Eastern')

            orders = trade.history_orders(
                start_at=datetime.now() - timedelta(days=days),
                end_at=datetime.now() + timedelta(days=1)
            )

            order_list = []
            for o in orders:
                if o.currency == 'USD':
                    order_list.append({
                        "symbol": cut_symbol(o.symbol),
                        "side": str(o.side),
                        "status": _format_order_status_for_llm(o.status),
                        "quantity": str(o.quantity),
                        "executed_quantity": str(o.executed_quantity),
                        "price": str(o.price) if o.price else "市价",
                        "executed_price": str(o.executed_price) if o.executed_price else "N/A",
                        "submitted_at": str(o.submitted_at.astimezone(eastern))
                    })

            return json.dumps({
                "orders": order_list[:20],  # 限制返回数量
                "count": len(order_list),
                "days": days
            })

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class GetQuoteTool(BaseTool):
    """获取股票报价工具"""

    name = "get_quote"
    description = "获取指定股票的实时报价信息"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如AAPL、TSLA等（不需要加.US后缀）"
        )
    ]

    def execute(self, symbol: str, **kwargs) -> str:
        try:
            config = get_longport_config()
            quote = QuoteContext(config)

            full_symbol = modify_symbol(symbol)
            result = quote.quote([full_symbol])

            if result:
                q = result[0]
                return json.dumps({
                    "symbol": cut_symbol(full_symbol),
                    "last_done": str(q.last_done),
                    "open": str(q.open),
                    "high": str(q.high),
                    "low": str(q.low),
                    "prev_close": str(q.prev_close),
                    "volume": str(q.volume),
                    "turnover": str(q.turnover),
                    "timestamp": str(q.timestamp)
                })
            else:
                return json.dumps({"error": f"无法获取{symbol}的报价"})

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class BuyStockTool(BaseTool):
    """买入股票工具"""

    name = "buy_stock"
    description = "买入股票，支持市价单(MO)、限价单(LO)、触及限价单(LIT)和触及市价单(MIT)"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如AAPL、TSLA等"
        ),
        ToolParameter(
            name="quantity",
            type="integer",
            description="买入数量（股数）"
        ),
        ToolParameter(
            name="order_type",
            type="string",
            description="订单类型: MO(市价), LO(限价), LIT(触及限价), MIT(触及市价), TSM(追踪止损金额), TSMPCT(追踪止损比例)",
            enum=["MO", "LO", "LIT", "MIT", "TSM", "TSMPCT"]
        ),
        ToolParameter(
            name="price",
            type="number",
            description="限价单(LO)或触及限价单(LIT)的限价",
            required=False
        ),
                ToolParameter(
            name="trigger_price",
            type="number",
            description="触及单(LIT/MIT)的触发价格",
            required=False
        ),
        ToolParameter(
            name="trailing_percent",
            type="number",
            description="追踪止损比例 (TSMPCT)",
            required=False
        ),
        ToolParameter(
            name="trailing_amount",
            type="number",
            description="追踪止损金额 (TSM)",
            required=False
        ),
        ToolParameter(
            name="reason",
            type="string",
            description="买入理由",
            required=False,
            default=""
        )
    ]

    def execute(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        trailing_percent: Optional[float] = None,
        trailing_amount: Optional[float] = None,
        reason: str = "",
        **kwargs
    ) -> str:
        try:
            from config import WATCHLIST
            
            # 1. 标的池硬拦截
            clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
            if clean_symbol not in WATCHLIST:
                error_msg = f"拦截: {symbol} 不在允许交易的标的池(WATCHLIST)中。当前仅允许交易: {WATCHLIST}"
                logging.warning(error_msg)
                return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)

            # 2. 风控评分硬拦截
            trade_logger = get_trade_logger()
            latest_risk = trade_logger.get_latest_risk_score()
            risk_score = latest_risk["score"] if latest_risk else 0
            sentiment = latest_risk["components"].get("sentiment", 50) if latest_risk and "components" in latest_risk else 50
            rsi_breadth = latest_risk["components"].get("rsi_breadth", 50) if latest_risk and "components" in latest_risk else 50

            # 按照风控规则，评分 < 50 (LOCKDOWN/CAUTIOUS) 时禁止建仓
            if risk_score > 0 and risk_score < 50:
                error_msg = f"风控拦截: 当前宏观评分 {risk_score} < 50 (CAUTIOUS/LOCKDOWN)，处于高风险模式，系统已硬性锁定买入权限，仅允许平仓/卖出。"
                logging.warning(error_msg)
                return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)

            # 按照风控规则，极度超买禁止使用左侧限价单(LO)
            if order_type == "LO" and (sentiment > 80 or rsi_breadth > 75):
                error_msg = f"风控拦截: 当前大盘极度超买 (Sentiment={sentiment:.1f}, RSI={rsi_breadth:.1f})，禁止使用左侧 LO 限价单在支撑位接飞刀！请改用右侧突破/确认单 (LIT)。"
                logging.warning(error_msg)
                return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)

            config = get_longport_config()
            trade = TradeContext(config)

            full_symbol = modify_symbol(symbol)
            
            # 自动撤销旧挂单防止冲突 (Cancel-Before-Modify)
            if order_type != "MO":
                _cancel_duplicate_pending_orders(trade, symbol, "Buy")

            # 动态决定订单有效期限 (Time in Force)
            # 市价单 (MO) 必须是当日有效 (Day)
            # 限价/条件单 (LO, LIT, 等) 使用撤销前有效 (GTC)，适合中长线趋势交易挂单
            if order_type == "MO":
                tif = TimeInForceType.Day
            else:
                tif = TimeInForceType.GoodTilCanceled
                
            order_params = {
                "side": OrderSide.Buy,
                "symbol": full_symbol,
                "submitted_quantity": Decimal(str(quantity)),
                "time_in_force": tif,
                "outside_rth": OutsideRTH.AnyTime,
                "remark": reason[:100] if reason else "AI Agent Buy Order"
            }

            if order_type == "LO":
                if price is None:
                    return json.dumps({"error": "限价单必须指定价格"}, ensure_ascii=False)
                adj_price, _ = _calculate_dynamic_slippage(symbol, price, "Buy", "LO")
                logging.info(f"动态滑点调整 (Buy LO): {price} -> {adj_price}")
                order_params["order_type"] = OrderType.LO
                order_params["submitted_price"] = Decimal(str(adj_price))
            elif order_type == "LIT":
                if trigger_price is None:
                    return json.dumps({"error": "触及限价单(LIT)必须指定 trigger_price"}, ensure_ascii=False)
                adj_price, _ = _calculate_dynamic_slippage(symbol, trigger_price, "Buy", "LIT")
                logging.info(f"动态滑点调整 (Buy LIT): 触发价 {trigger_price} -> 限价 {adj_price}")
                order_params["order_type"] = OrderType.LIT
                order_params["submitted_price"] = Decimal(str(adj_price))
                order_params["trigger_price"] = Decimal(str(trigger_price))
            elif order_type == "MIT":
                if trigger_price is None:
                    return json.dumps({"error": "触及市价单(MIT)必须指定 trigger_price"}, ensure_ascii=False)
                order_params["order_type"] = OrderType.MIT
                order_params["trigger_price"] = Decimal(str(trigger_price))
            elif order_type == "TSMPCT":
                if trailing_percent is None:
                    return json.dumps({"error": "追踪止损百分比单(TSMPCT)必须指定 trailing_percent"}, ensure_ascii=False)
                order_params["order_type"] = OrderType.TSLPPCT
                order_params["trailing_percent"] = Decimal(str(trailing_percent))
                _, dynamic_offset = _calculate_dynamic_slippage(symbol, 100, "Buy", "TSM")
                order_params["limit_offset"] = Decimal(str(dynamic_offset))
            elif order_type == "TSM":
                if trailing_amount is None:
                    return json.dumps({"error": "追踪止损金额单(TSM)必须指定 trailing_amount"}, ensure_ascii=False)
                order_params["order_type"] = OrderType.TSLPAMT
                order_params["trailing_amount"] = Decimal(str(trailing_amount))
                _, dynamic_offset = _calculate_dynamic_slippage(symbol, 100, "Buy", "TSM")
                order_params["limit_offset"] = Decimal(str(dynamic_offset))
            else:
                order_params["order_type"] = OrderType.MO

            resp = trade.submit_order(**order_params)

            # 记录交易日志
            trade_logger = get_trade_logger()
            latest_risk = trade_logger.get_latest_risk_score()
            risk_score = latest_risk["score"] if latest_risk else 0
            trade_logger.log_trade(
                symbol=cut_symbol(full_symbol),
                side="Buy",
                quantity=quantity,
                price=price,
                order_type=order_type,
                order_id=resp.order_id,
                reason=reason,
                risk_score=risk_score,
            )

            status_msg = "已成交 (FILLING/MO)" if order_type == "MO" else "已挂单 (PENDING/WAITING)"
            execution_hint = "该订单为限价/触及单，仅在价格满足条件时成交。请在后续循环中通过 get_today_orders 确认其实际状态。"

            return json.dumps({
                "success": True,
                "order_id": resp.order_id,
                "symbol": cut_symbol(full_symbol),
                "side": "Buy",
                "quantity": quantity,
                "order_type": order_type,
                "price": price if order_type in ["LO", "LIT"] else "市价",
                "trigger_price": trigger_price if order_type in ["LIT", "MIT"] else None,
                "status": status_msg,
                "message": f"买入指令下达成功 [{status_msg}]: {quantity}股 {symbol}。{execution_hint}"
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e), "success": False}, ensure_ascii=False)


class SellStockTool(BaseTool):
    """卖出股票工具"""

    name = "sell_stock"
    description = "卖出股票，支持市价单(MO)和限价单(LO)"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如AAPL、TSLA等"
        ),
        ToolParameter(
            name="quantity",
            type="integer",
            description="卖出数量（股数）"
        ),
        ToolParameter(
            name="order_type",
            type="string",
            description="订单类型: MO(市价), LO(限价), LIT(触及限价), MIT(触及市价), TSM(追踪止损金额), TSMPCT(追踪止损比例)",
            enum=["MO", "LO", "LIT", "MIT", "TSM", "TSMPCT"]
        ),
        ToolParameter(
            name="price",
            type="number",
            description="限价单(LO)或触及限价单(LIT)的限价",
            required=False
        ),
        ToolParameter(
            name="trigger_price",
            type="number",
            description="触及单(LIT/MIT)的触发价格",
            required=False
        ),
        ToolParameter(
            name="trailing_percent",
            type="number",
            description="追踪止损比例 (TSMPCT)",
            required=False
        ),
        ToolParameter(
            name="trailing_amount",
            type="number",
            description="追踪止损金额 (TSM)",
            required=False
        ),
        ToolParameter(
            name="reason",
            type="string",
            description="卖出理由",
            required=False,
            default=""
        )
    ]

    def execute(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        trailing_percent: Optional[float] = None,
        trailing_amount: Optional[float] = None,
        reason: str = "",
        **kwargs
    ) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)

            full_symbol = modify_symbol(symbol)
            clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol

            # 安全校验：获取真实持仓，防止因状态未同步导致重复卖出或意外做空
            try:
                positions_resp = trade.stock_positions()
                my_qty = Decimal('0')
                if hasattr(positions_resp, 'channels'):
                    for channel in positions_resp.channels:
                        for pos in channel.positions:
                            pos_sym = getattr(pos, 'symbol', '')
                            if clean_symbol in pos_sym or full_symbol in pos_sym:
                                my_qty += getattr(pos, 'quantity', Decimal('0'))
                
                if my_qty == Decimal('0'):
                    error_msg = f"卖出拦截: 当前未持有 {symbol}，无法执行卖出操作（防止做空）。"
                    logging.warning(error_msg)
                    return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)
                
                # 限制最大卖出量为当前持仓量
                if Decimal(str(quantity)) > my_qty:
                    logging.warning(f"卖出数量 {quantity} 超过实际持仓 {my_qty}，自动修正为 {my_qty}")
                    quantity = int(my_qty)
            except Exception as e:
                logging.warning(f"获取持仓进行卖出前校验时出错: {e}，将继续尝试下发订单。")

            # 自动撤销旧挂单防止冲突 (Cancel-Before-Modify)
            if order_type != "MO":
                _cancel_duplicate_pending_orders(trade, symbol, "Sell")

            # 动态决定订单有效期限 (Time in Force)
            if order_type == "MO":
                tif = TimeInForceType.Day
            else:
                tif = TimeInForceType.GoodTilCanceled

            order_params = {
                "side": OrderSide.Sell,
                "symbol": full_symbol,
                "submitted_quantity": Decimal(str(quantity)),
                "time_in_force": tif,
                "outside_rth": OutsideRTH.AnyTime,
                "remark": reason[:100] if reason else "AI Agent Sell Order"
            }

            if order_type == "LO":
                if price is None:
                    return json.dumps({"error": "限价单必须指定价格"}, ensure_ascii=False)
                adj_price, _ = _calculate_dynamic_slippage(symbol, price, "Sell", "LO")
                logging.info(f"动态滑点调整 (Sell LO): {price} -> {adj_price}")
                order_params["order_type"] = OrderType.LO
                order_params["submitted_price"] = Decimal(str(adj_price))
            elif order_type == "LIT":
                if trigger_price is None:
                    return json.dumps({"error": "触及限价单(LIT)必须指定 trigger_price"}, ensure_ascii=False)
                adj_price, _ = _calculate_dynamic_slippage(symbol, trigger_price, "Sell", "LIT")
                logging.info(f"动态滑点调整 (Sell LIT): 触发价 {trigger_price} -> 限价 {adj_price}")
                order_params["order_type"] = OrderType.LIT
                order_params["submitted_price"] = Decimal(str(adj_price))
                order_params["trigger_price"] = Decimal(str(trigger_price))
            elif order_type == "MIT":
                if trigger_price is None:
                    return json.dumps({"error": "触及市价单(MIT)必须指定 trigger_price"}, ensure_ascii=False)
                order_params["order_type"] = OrderType.MIT
                order_params["trigger_price"] = Decimal(str(trigger_price))
            elif order_type == "TSMPCT":
                if trailing_percent is None:
                    return json.dumps({"error": "追踪止损百分比单(TSMPCT)必须指定 trailing_percent"}, ensure_ascii=False)
                
                # ── 新增: Trailing Stop Sanity Check ──
                orig_trailing_percent = trailing_percent
                if trailing_percent > 12.0:
                    trailing_percent = 12.0
                    logging.warning(f"⚠️ [Sanity Check] 检测到追踪止损百分比过宽 ({orig_trailing_percent}%)，自动缩限为 12.0%，以防严重吞食利润和亏损本金。")
                elif trailing_percent < 3.0:
                    trailing_percent = 3.0
                    logging.warning(f"⚠️ [Sanity Check] 检测到追踪止损百分比过窄 ({orig_trailing_percent}%)，自动放大到 3.0%，以防频繁被无谓的日内震荡噪声洗盘扫出。")
                
                order_params["order_type"] = OrderType.TSLPPCT
                order_params["trailing_percent"] = Decimal(str(trailing_percent))
                _, dynamic_offset = _calculate_dynamic_slippage(symbol, 100, "Buy", "TSM")
                order_params["limit_offset"] = Decimal(str(dynamic_offset))
            elif order_type == "TSM":
                if trailing_amount is None:
                    return json.dumps({"error": "追踪止损金额单(TSM)必须指定 trailing_amount"}, ensure_ascii=False)
                order_params["order_type"] = OrderType.TSLPAMT
                order_params["trailing_amount"] = Decimal(str(trailing_amount))
                _, dynamic_offset = _calculate_dynamic_slippage(symbol, 100, "Buy", "TSM")
                order_params["limit_offset"] = Decimal(str(dynamic_offset))
            else:
                order_params["order_type"] = OrderType.MO

            resp = trade.submit_order(**order_params)

            # 记录交易日志
            trade_logger = get_trade_logger()
            latest_risk = trade_logger.get_latest_risk_score()
            risk_score = latest_risk["score"] if latest_risk else 0
            trade_logger.log_trade(
                symbol=cut_symbol(full_symbol),
                side="Sell",
                quantity=quantity,
                price=price,
                order_type=order_type,
                order_id=resp.order_id,
                reason=reason,
                risk_score=risk_score,
            )

            status_msg = "已成交 (FILLING/MO)" if order_type == "MO" else "已挂单 (PENDING/WAITING)"
            execution_hint = "该订单已提交。若是限价单或追踪止损单，需满足价格条件方可成交。"

            return json.dumps({
                "success": True,
                "order_id": resp.order_id,
                "symbol": cut_symbol(full_symbol),
                "side": "Sell",
                "quantity": quantity,
                "order_type": order_type,
                "price": price if order_type == "LO" else "市价",
                "status": status_msg,
                "message": f"卖出指令下达成功 [{status_msg}]: {quantity}股 {symbol}。{execution_hint}"
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e), "success": False}, ensure_ascii=False)



class CancelOrderTool(BaseTool):
    """撤销订单工具"""

    name = "cancel_order"
    description = "撤销尚未成交的挂单 (PENDING/WAITING 状态的订单)"
    parameters = [
        ToolParameter(
            name="order_id",
            type="string",
            description="需要撤销的订单 ID"
        ),
        ToolParameter(
            name="reason",
            type="string",
            description="撤单理由",
            required=False,
            default=""
        )
    ]

    def execute(self, order_id: str, reason: str = "", **kwargs) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)
            trade.cancel_order(order_id)
            logging.info(f"成功下达撤单指令: {order_id}, 理由: {reason}")
            return json.dumps({"success": True, "order_id": order_id, "message": "撤单指令已发送成功"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

def create_trading_tools() -> list[BaseTool]:
    """
    创建所有交易工具实例

    Returns:
        交易工具列表
    """
    return [
        GetMarketStatusTool(),
        GetPositionsTool(),
        GetAccountBalanceTool(),
        GetTodayOrdersTool(),
        GetHistoryOrdersTool(),
        CancelOrderTool(),
        GetQuoteTool(),
        BuyStockTool(),
        SellStockTool(),
    ]


def auto_align_trailing_stops() -> str:
    """
    自动对齐/重整当前持仓与长桥挂设的追踪比例止损单（防漏挂、数量错配）。
    检查当前所有持仓。如果某只股票在券商端已挂设了追踪百分比止损单，但挂单的股数不等于最新实际持仓股数，
    则自动撤销该标的旧止损单，并以最新的全额持仓股数、维持先前的追踪比例重新挂设。
    """
    try:
        from longport.openapi import TradeContext, OrderType, OrderSide
        import logging
        from decimal import Decimal
        
        logging.info("⚙️ [Alignment] 启动追踪止损数量自动重整/对齐校验...")
        config = get_longport_config()
        trade = TradeContext(config)
        
        # 1. 获取最新持仓
        positions_resp = trade.stock_positions()
        pos_map = {}
        if hasattr(positions_resp, 'channels'):
            for channel in positions_resp.channels:
                for pos in channel.positions:
                    qty = getattr(pos, 'quantity', Decimal('0'))
                    if qty > Decimal('0'):
                        pos_map[cut_symbol(pos.symbol)] = int(qty)
        
        if not pos_map:
            logging.info("⚙️ [Alignment] 当前无持仓，无需对齐。")
            return "No positions found."
            
        # 2. 获取所有的今日订单 (即挂单)
        orders = trade.today_orders()
        
        # 3. 找出所有活跃的（PENDING_CONDITIONAL 等）追踪比例止损单
        pending_stops = {}
        for o in orders:
            status_str = str(o.status).lower()
            is_pending = any(s in status_str for s in ["notreported", "new", "submitted", "pending", "partialfilled", "varietiesnotreported"])
            is_sell = o.side == OrderSide.Sell
            is_tslppct = o.order_type == OrderType.TSLPPCT
            
            if is_pending and is_sell and is_tslppct:
                sym = cut_symbol(o.symbol)
                pending_stops[sym] = {
                    "order_id": o.order_id,
                    "quantity": int(o.quantity),
                    "trailing_percent": float(o.trailing_percent) if o.trailing_percent else 8.0,
                    "limit_offset": o.limit_offset
                }
        
        if not pending_stops:
            logging.info("⚙️ [Alignment] 当前没有生效中的追踪百分比止损单挂设，无需自动对齐数量。")
            return "No pending TSLPPCT stops found."
            
        # 4. 比对持仓和止损单的数量
        realigned_count = 0
        for sym, stop in pending_stops.items():
            if sym in pos_map:
                actual_qty = pos_map[sym]
                stop_qty = stop["quantity"]
                
                if actual_qty != stop_qty:
                    logging.warning(
                        f"🚨 [Alignment] 侦测到持仓数量与止损挂单数量不一致! "
                        f"标时: {sym}, 实际持仓: {actual_qty}股, 止损挂单: {stop_qty}股。"
                    )
                    
                    # 1) 撤销该旧单
                    try:
                        logging.info(f"⏳ [Alignment] 正在撤销旧的错配止损单: {stop['order_id']}...")
                        trade.cancel_order(stop["order_id"])
                        import time
                        time.sleep(0.5)  # 短暂休眠等额度释放
                    except Exception as ex:
                        logging.error(f"❌ [Alignment] 撤销旧止损单失败: {ex}")
                        continue
                        
                    # 2) 重新以最新全额持仓股数挂设
                    try:
                        from tools.trading import modify_symbol, _calculate_dynamic_slippage
                        from longport.openapi import TimeInForceType, OutsideRTH
                        
                        full_symbol = modify_symbol(sym)
                        trailing_pct = stop["trailing_percent"]
                        
                        # 基于标的计算最新的动态 limit_offset
                        _, dynamic_offset = _calculate_dynamic_slippage(sym, 100, "Buy", "TSM")
                        
                        order_params = {
                            "side": OrderSide.Sell,
                            "symbol": full_symbol,
                            "submitted_quantity": Decimal(str(actual_qty)),
                            "time_in_force": TimeInForceType.GoodTilCanceled,
                            "outside_rth": OutsideRTH.AnyTime,
                            "order_type": OrderType.TSLPPCT,
                            "trailing_percent": Decimal(str(trailing_pct)),
                            "limit_offset": Decimal(str(dynamic_offset)),
                            "remark": f"[Auto-Alignment] 自动对齐全额持仓止损 ({trailing_pct}%)"
                        }
                        
                        new_resp = trade.submit_order(**order_params)
                        logging.info(
                            f"✅ [Alignment] {sym} 追踪止损重整提交成功！"
                            f"股数: {actual_qty}股, 止损比例: {trailing_pct}%, 新订单ID: {new_resp.order_id}"
                        )
                        
                        # 记录到交易日志
                        try:
                            from data.trade_logger import get_trade_logger
                            trade_logger = get_trade_logger()
                            latest_risk = trade_logger.get_latest_risk_score()
                            risk_score = latest_risk["score"] if latest_risk else 0
                            trade_logger.log_trade(
                                symbol=sym,
                                side="Sell",
                                quantity=actual_qty,
                                price=None,
                                order_type="TSMPCT",
                                order_id=new_resp.order_id,
                                reason=f"【自动对齐】原止损股数错配，重新全额挂设 {actual_qty}股 追踪止损 ({trailing_pct}%)。",
                                risk_score=risk_score
                            )
                        except Exception as log_ex:
                            logging.warning(f"[Alignment] 记录对齐交易日志时出错: {log_ex}")
                            
                        realigned_count += 1
                    except Exception as sub_ex:
                        logging.error(f"❌ [Alignment] 重新下达止损挂单失败: {sub_ex}")
        
        logging.info(f"⚙️ [Alignment] 自动校验校验结束。共重整对齐了 {realigned_count} 个标的。")
        return f"Successfully realigned {realigned_count} stops."
        
    except Exception as e:
        import traceback
        logging.error(f"❌ [Alignment] 自动对齐异常: {e}, 堆栈: {traceback.format_exc()}")
        return f"Error: {e}"
