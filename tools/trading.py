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
from tools.engines import get_trading_engine



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
            from tools.market_data import get_quote_ctx, modify_symbol
            engine = get_trading_engine()
            positions_data = engine.get_positions()
            
            positions = []
            symbols = [modify_symbol(pos["symbol"]) for pos in positions_data]
            
            # 批量获取现价
            quotes_map = {}
            if symbols:
                try:
                    quote_ctx = get_quote_ctx()
                    quote_res = quote_ctx.quote(symbols)
                    for q in quote_res:
                        quotes_map[q.symbol] = q.last_done
                except Exception as e:
                    logging.warning(f"GetPositionsTool 批量获取现价失败: {e}")

            for pos in positions_data:
                sym = pos["symbol"]
                last_done = quotes_map.get(modify_symbol(sym))
                cost_price = pos["cost_price"]
                
                profit_pct = 0.0
                if last_done and cost_price > 0:
                    profit_pct = (float(last_done) - cost_price) / cost_price * 100

                positions.append({
                    "symbol": cut_symbol(sym),
                    "quantity": str(pos["quantity"]),
                    "cost_price": str(cost_price),
                    "last_done": str(last_done) if last_done else "N/A",
                    "profit_pct": f"{profit_pct:.2f}%",
                    "currency": "USD",
                    "market_value": str(pos["market_value"])
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
            engine = get_trading_engine()
            bal = engine.get_account_balance()

            result = {
                "net_assets": str(bal["net_assets"]),
                "total_cash": str(bal["cash"]),
                "currency": "USD"
            }
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
            engine = get_trading_engine()
            orders = engine.get_today_orders()
            order_list = []

            eastern = pytz.timezone('US/Eastern')
            for o in orders:
                raw_order = o["raw_order"]
                submitted_at_str = "N/A"
                if hasattr(raw_order, 'submitted_at') and raw_order.submitted_at:
                    submitted_at_str = str(raw_order.submitted_at.astimezone(eastern))
                
                order_list.append({
                    "symbol": cut_symbol(o["symbol"]),
                    "side": o["side"],
                    "status": o["llm_status"],
                    "quantity": str(o["quantity"]),
                    "executed_quantity": str(getattr(raw_order, 'executed_quantity', '0')),
                    "price": str(o["price"]) if o["price"] else "市价",
                    "executed_price": str(getattr(raw_order, 'executed_price', 'N/A')) if getattr(raw_order, 'executed_price', None) else "N/A",
                    "submitted_at": submitted_at_str
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
            engine = get_trading_engine()
            orders = engine.get_history_orders(days)
            order_list = []

            eastern = pytz.timezone('US/Eastern')
            for o in orders:
                raw_order = o["raw_order"]
                submitted_at_str = "N/A"
                if hasattr(raw_order, 'submitted_at') and raw_order.submitted_at:
                    submitted_at_str = str(raw_order.submitted_at.astimezone(eastern))
                
                order_list.append({
                    "symbol": cut_symbol(o["symbol"]),
                    "side": o["side"],
                    "status": o["llm_status"],
                    "quantity": str(o["quantity"]),
                    "executed_quantity": str(getattr(raw_order, 'executed_quantity', '0')),
                    "price": str(o["price"]) if o["price"] else "市价",
                    "executed_price": str(getattr(raw_order, 'executed_price', 'N/A')) if getattr(raw_order, 'executed_price', None) else "N/A",
                    "submitted_at": submitted_at_str
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
    """买入股票或期权工具"""

    name = "buy_stock"
    description = "买入股票或期权合约。支持市价单(MO)、限价单(LO)、触及限价单(LIT)和触及市价单(MIT)。自动判定代码格式，对于期权合约自动转换乘数及自适应专属大滑点。"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码或期权合约代码（如 AAPL.US 或 AAPL260619C00200000.US）"
        ),
        ToolParameter(
            name="quantity",
            type="integer",
            description="买入股数。如果是期权合约，输入你想保护的正股股数即可，系统会自动转换（除以 100）并向下换算为期权张数，向上取整最少买入 1 张。"
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
            from tools.engines.base import BaseTradingEngine
            
            # 1. 标的池硬拦截 (仅在非期权合约时才进行标的池硬性拦截)
            clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
            if clean_symbol not in WATCHLIST and not BaseTradingEngine.is_option_symbol(symbol):
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

            # 3. 突破单情绪分仓惩罚与假突破确认 (痛点一优化)
            # 如果是买入市价单(MO)且理由包含突破、追高、打穿等字眼
            is_breakout_buy = (order_type == "MO") and any(w in reason.lower() or w in str(kwargs).lower() for w in ["突破", "追高", "打穿", "breakout", "offensive"])
            
            # (1) 分仓减半处罚：如果大盘情绪极其低迷 (Sentiment < 40)，下单股数强制减半
            if sentiment < 40:
                original_qty = quantity
                quantity = max(1, quantity // 2)
                reason += f" [风控干预: 当前情绪分 Sentiment={sentiment:.1f}<40, 触发分仓减半处罚, 原始股数 {original_qty} -> 惩罚后股数 {quantity}]"
                logging.warning(f"[Sentiment Position Penalty] Sentiment={sentiment:.1f} < 40, Quantity cut from {original_qty} to {quantity}")

            # (2) 假突破防接飞刀过滤：在低情绪/弱市环境 (Sentiment < 50) 下，禁止使用市价单(MO)直接追高突破，自动转换为带价格缓冲的回踩限价单(LO)
            if is_breakout_buy and sentiment < 50:
                try:
                    config_lp = get_longport_config()
                    quote_ctx = QuoteContext(config_lp)
                    quote_res = quote_ctx.quote([modify_symbol(symbol)])
                    if quote_res:
                        current_price = float(quote_res[0].last_done)
                        # 将市价单(MO)降级为带价格缓冲的回踩 0.6% 确认限价单(LO)，防范假突破套牢
                        order_type = "LO"
                        price = round(current_price * 0.994, 2)
                        reason += f" [风控干预: 弱市(Sentiment={sentiment:.1f}<50)突破追高风险大, 强制将市价单(MO)降级为0.6%回踩限价单(LO), 挂单价 ${price}]"
                        logging.warning(f"[Fake Breakout Filter] Downgraded breakout MO to pullback LO @ ${price} for symbol {symbol}")
                except Exception as e:
                    logging.error(f"[Fake Breakout Filter] 查询报价失败，无法执行回踩确认: {e}")

            engine = get_trading_engine()
            
            # 直接调用统一的 submit_order
            resp = engine.submit_order(
                symbol=symbol,
                side="Buy",
                order_type=order_type,
                quantity=quantity,
                price=price,
                trigger_price=trigger_price,
                trailing_percent=trailing_percent,
                trailing_amount=trailing_amount,
                reason=reason
            )

            # 记录交易日志
            trade_logger.log_trade(
                symbol=cut_symbol(symbol),
                side="Buy",
                quantity=quantity,
                price=price,
                order_type=order_type,
                order_id=resp["order_id"],
                reason=reason,
                risk_score=risk_score,
            )

            status_msg = "已成交 (FILLING/MO)" if order_type == "MO" else "已挂单 (PENDING/WAITING)"
            execution_hint = "该订单已提交。请在后续循环中通过 get_today_orders 确认其实际状态。"

            return json.dumps({
                "success": True,
                "order_id": resp["order_id"],
                "symbol": cut_symbol(symbol),
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
    """卖出股票或期权工具"""

    name = "sell_stock"
    description = "卖出股票或期权合约（平仓）。支持市价单(MO)、限价单(LO)、触及限价单(LIT)、触及市价单(MIT)、追踪止损单等。自动判定代码格式并自适应股张数量换算。"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码或期权合约代码（如 AAPL.US 或 AAPL260619C00200000.US）"
        ),
        ToolParameter(
            name="quantity",
            type="integer",
            description="卖出数量。若是期权合约，输入等效的正股股数即可，系统会自动转换（除以 100）换算为期权张数进行平仓。"
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
            engine = get_trading_engine()

            full_symbol = modify_symbol(symbol)
            clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol

            # 安全校验：获取真实持仓，防止因状态未同步导致重复卖出或意外做空
            cost_price = None
            try:
                positions_data = engine.get_positions()
                my_qty = Decimal('0')
                for pos in positions_data:
                    pos_sym = pos["symbol"]
                    if clean_symbol in pos_sym or full_symbol in pos_sym:
                        my_qty += Decimal(str(pos["quantity"]))
                        cost_price = float(pos["cost_price"])

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

            # Trailing Stop Sanity Check (追踪止损比例微调限制)
            if order_type == "TSMPCT" and trailing_percent is not None:
                # 🛡️ 引入高级非线性 PRR (Profit Retention Ratio) 利润留存算法保护
                # 当正股出现浮盈时，若 ATR 止损过宽会导致在下跌时吞食所有本金和浮盈。
                # 只要正股脱离风险期，我们动态收紧追踪比例，保证如果回调触发，至少锁死 50% 的浮盈利润。
                if cost_price is not None:
                    try:
                        from tools.market_data import get_quote_ctx
                        ctx = get_quote_ctx()
                        quotes = ctx.quote([full_symbol])
                        if quotes:
                            current_price = float(quotes[0].last_done)
                            if current_price > cost_price:
                                # 锁定最高浮盈 50% 对应的目标追踪比例: T_target = (0.5 * (P - C) / P) * 100
                                t_target = (0.5 * (current_price - cost_price) / current_price) * 100.0
                                # 仅在利润空间脱离风险期（目标止损比 > 3% 的波动噪声垫）且计算值比原本 ATR 计算出的比例更窄时，才强行收缩收网
                                if t_target >= 3.0 and t_target < trailing_percent:
                                    orig_trailing_percent = trailing_percent
                                    trailing_percent = round(t_target, 2)
                                    logging.info(
                                        f"🛡️ [PRR Guard] 侦测到持仓 {symbol} 处于盈利状态 (成本: ${cost_price:.2f}, 现价: ${current_price:.2f})。"
                                        f"为了在下跌中锁定至少 50% 的浮盈，动态利润留存锁启动，追踪比例由 {orig_trailing_percent}% 收网收紧至 {trailing_percent}%！"
                                    )
                    except Exception as prr_err:
                        logging.warning(f"[PRR Guard] 利润保护计算时发生异常: {prr_err}")
                orig_trailing_percent = trailing_percent
                # 配合 V3.5+ 自适应 ATR 机制，将原 12% 上限拓宽至 25%，给予高波动股票（如 ARM, NVDA）在主升浪里足够呼吸空间
                if trailing_percent > 25.0:
                    trailing_percent = 25.0
                    logging.warning(f"⚠️ [Sanity Check] 检测到追踪止损百分比过宽 ({orig_trailing_percent}%)，自动缩限为 25.0%，以防大模型幻觉并限制极端利润回吐。")
                elif trailing_percent < 3.0:
                    trailing_percent = 3.0
                    logging.warning(f"⚠️ [Sanity Check] 检测到追踪止损百分比过窄 ({orig_trailing_percent}%)，自动放大到 3.0%，以防频繁被无谓的日内震荡噪声洗盘扫出。")

            # 直接调用适配器的 submit_order
            resp = engine.submit_order(
                symbol=symbol,
                side="Sell",
                order_type=order_type,
                quantity=quantity,
                price=price,
                trigger_price=trigger_price,
                trailing_percent=trailing_percent,
                trailing_amount=trailing_amount,
                reason=reason
            )

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
                order_id=resp["order_id"],
                reason=reason,
                risk_score=risk_score,
            )

            status_msg = "已成交 (FILLING/MO)" if order_type == "MO" else "已挂单 (PENDING/WAITING)"
            execution_hint = "该订单已提交。请在后续循环中通过 get_today_orders 确认其实际状态。"

            return json.dumps({
                "success": True,
                "order_id": resp["order_id"],
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
            engine = get_trading_engine()
            success = engine.cancel_order(order_id)
            if success:
                logging.info(f"成功下达撤单指令: {order_id}, 理由: {reason}")
                return json.dumps({"success": True, "order_id": order_id, "message": "撤单指令已发送成功"}, ensure_ascii=False)
            else:
                return json.dumps({"success": False, "error": "撤单失败，请检查订单状态"}, ensure_ascii=False)
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
