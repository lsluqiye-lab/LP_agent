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
    from tools.market_data import get_quote_ctx
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
            from tools.market_data import get_quote_ctx
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
                    "order_id": str(o["order_id"]),
                    "symbol": cut_symbol(o["symbol"]),
                    "side": o["side"],
                    "status": o["llm_status"],
                    "quantity": str(o["quantity"]),
                    "executed_quantity": str(o["executed_quantity"]),
                    "price": str(o["price"]) if o["price"] else "市价",
                    "trailing_percent": o.get("trailing_percent"),
                    "trailing_amount": o.get("trailing_amount"),
                    "trigger_price": o.get("trigger_price"),
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
                    "order_id": str(o["order_id"]),
                    "symbol": cut_symbol(o["symbol"]),
                    "side": o["side"],
                    "status": o["llm_status"],
                    "quantity": str(o["quantity"]),
                    "executed_quantity": str(o["executed_quantity"]),
                    "price": str(o["price"]) if o["price"] else "市价",
                    "trailing_percent": o.get("trailing_percent"),
                    "trailing_amount": o.get("trailing_amount"),
                    "trigger_price": o.get("trigger_price"),
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
            from tools.market_data import get_quote_ctx
            quote_ctx = get_quote_ctx()

            full_symbol = modify_symbol(symbol)
            result = quote_ctx.quote([full_symbol])

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
    description = "买入股票或期权合约。支持多种订单类型。内设物理风控拦截：1. 单股持仓上限 (5%-15%); 2. 账户总杠杆上限 (1.0x-1.5x); 3. 对冲上限。支持高胜率领涨股(Tier 1 Leader)特权模式。"
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
            name="conviction",
            type="string",
            description="信心等级：'normal'(默认) 或 'high'。设置为 'high' 将标记为 Tier 1 Leader，自动应用更宽容的防守策略以博取翻倍收益。",
            required=False,
            enum=["normal", "high"],
            default="normal"
        ),
        ToolParameter(
            name="force_recovery",
            type="boolean",
            description="V型反转强制回补：设置为 true 可豁免最近 3 天的 HARD_STOP 冷静期拦截。仅限观察到巨量(>2.0x)收复失地且为 Tier 1 标的时使用。",
            required=False,
            default=False
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
        conviction: str = "normal",
        force_recovery: bool = False,
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

            # 🚨 新增：原子化仓位预校验与自动缩减 (Atomic Position Scaling)
            try:
                from agent.portfolio_manager import PortfolioManager
                pm = PortfolioManager()
                
                # (A) 增加硬止损冷静期拦截 (Whipsaw Protection)
                # 如果该标的在过去 3 天内刚触发过硬止损，禁止立刻买回
                engine = get_trading_engine()
                account_balance = engine.get_account_balance()
                current_pos_resp = engine.get_positions()
                pm_report = pm.analyze_portfolio(current_pos_resp, account_balance, {"score": risk_score})
                
                # 🚨 升级 V4.6：板块资金流向惩罚感知 (Sector Flow Penalty Awareness)
                sector_penalties = pm_report.get("sector_flow_penalties", {})
                if sector_penalties:
                    # 物理层仅记录，由 CIO 在 ReAct 层面执行缩减逻辑
                    logging.info(f"[Physical Guard] 检测到板块资金流向惩罚: {sector_penalties}。已确认 CIO 决策中的合规性。")

                for weed in pm_report.get("recently_weeded_out", []):
                    if weed["symbol"] == clean_symbol:
                        if weed["type"] == "INTRADAY_STOP":
                            # 日内冷静期是绝对锁死，即便 force_recovery 也不允许
                            error_msg = f"物理离场拦截: {symbol} 在 {weed.get('hours_left', 4)} 小时前刚执行过离场操作。根据宪法，严禁在同一交易时段(4h内)反复进出。请保持观察，等待情绪企稳。"
                            logging.warning(error_msg)
                            return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)
                            
                        elif weed["type"] == "HARD_STOP":
                            if force_recovery and conviction == "high":
                                logging.info(f"🛡️ [V-Recovery] 检测到对 {symbol} 的强力回补指令且具备 High Conviction，豁免 3 天冷静期拦截执行买入。")
                                reason += " [V-Recovery 纠偏回补]"
                            else:
                                error_msg = f"Whipsaw 拦截: {symbol} 正处于止损离场后的 {weed['days_left']} 天冷静保护期内。系统已硬性拦截该买入指令，防止左右挨打。若确认为 V 型反转且在 4h 观察期后，请使用 force_recovery=True 且 conviction='high' 强行回补。"
                                logging.warning(error_msg)
                                return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)

                # (B) 获取动态上限 (基于当前风险评分)
                max_stock_limit_pct = 5.0 + (risk_score / 100.0) * 10.0 # 5% - 15%
                if conviction == "high":
                    max_stock_limit_pct = min(18.0, max_stock_limit_pct * 1.2) # High Conviction 允许额外 20% 溢价空间，上限 18%
                
                # 获取当前账户状态
                engine = get_trading_engine()
                balance = engine.get_account_balance()
                net_assets = float(balance.get("net_assets", 0))
                cash = float(balance.get("cash", 0))

                if net_assets > 0:
                    # 1. 🛡️ 杠杆率硬拦截 (Leverage Guard)
                    # 理论持仓总市值 = 净资产 - 现金 (现金为负代表融资)
                    current_leverage = (net_assets - cash) / net_assets
                    # 动态最大杠杆：评分 100 时 1.5x，评分 0 时 1.0x (禁止任何融资)
                    max_leverage = 1.0 + (risk_score / 100.0) * 0.5
                    
                    if current_leverage > max_leverage:
                        error_msg = f"杠杆拦截: 当前账户总杠杆率 {current_leverage:.2f}x 已超过风控上限 {max_leverage:.2f}x (评分: {risk_score})。禁止新开仓位，请先平仓减速。"
                        logging.warning(error_msg)
                        return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)

                    positions_resp = engine.get_positions()
                    
                    # 2. 🛡️ 对冲头寸总额拦截 (Hedge Over-protection Guard)
                    # 如果当前在买入 Put 期权，检查全局 Put 占比
                    is_put = BaseTradingEngine.is_option_symbol(symbol) and ("P" in symbol or "PUT" in symbol.upper())
                    if is_put:
                        total_put_value = 0
                        for p in positions_resp:
                            p_sym = p["symbol"]
                            if BaseTradingEngine.is_option_symbol(p_sym) and ("P" in p_sym or "PUT" in p_sym.upper()):
                                total_put_value += abs(float(p.get("market_value", 0)))
                        
                        max_hedge_pct = 15.0 # 总对冲市值上限 15%
                        current_hedge_pct = (total_put_value / net_assets) * 100.0
                        if current_hedge_pct > max_hedge_pct:
                            error_msg = f"对冲拦截: 当前 Put 总市值占比 {current_hedge_pct:.2f}% 已达到上限 {max_hedge_pct}%。禁止进一步过度对冲，防止权利金无谓损耗。"
                            logging.warning(error_msg)
                            return json.dumps({"error": error_msg, "success": False}, ensure_ascii=False)

                    current_pos_value = 0
                    for p in positions_resp:
                        if cut_symbol(p["symbol"]) == clean_symbol:
                            current_pos_value = float(p.get("market_value", 0))
                            break
                    
                    # 获取当前现价用于计算预估价值
                    from tools.market_data import get_quote_ctx
                    quote_ctx = get_quote_ctx()
                    quote_res = quote_ctx.quote([modify_symbol(symbol)])
                    est_price = float(quote_res[0].last_done) if quote_res else (price or trigger_price or 0)
                    
                    if est_price > 0:
                        # 计算单笔最大允许增持金额
                        allowed_total_value = net_assets * (max_stock_limit_pct / 100.0)
                        remaining_quota = max(0, allowed_total_value - current_pos_value)
                        
                        requested_value = est_price * quantity
                        if requested_value > remaining_quota:
                            scaled_qty = int(remaining_quota / est_price)
                            if scaled_qty < quantity:
                                original_qty = quantity
                                quantity = scaled_qty
                                if quantity <= 0:
                                    return json.dumps({"error": f"仓位拦截: {symbol} 当前持仓已达上限({max_stock_limit_pct}%)，无法继续买入。", "success": False}, ensure_ascii=False)
                                reason += f" [仓位自动缩减: 触发单股上限 {max_stock_limit_pct}%, 股数 {original_qty} -> {quantity}]"
                                logging.warning(f"[Position Scaling] {symbol} quantity scaled from {original_qty} to {quantity} to stay within {max_stock_limit_pct}% limit.")
            except Exception as e:
                logging.error(f"[Position Scaling] 预校验过程出错: {e}")

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
                    from tools.market_data import get_quote_ctx
                    quote_ctx = get_quote_ctx()
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
            
            # 🚨 核心增强：引入买单 Hysteresis 拦截，防止高频重复挂单
            if order_type in ["LO", "LIT", "MIT"]:
                try:
                    today_orders = engine.get_today_orders()
                    for o in today_orders:
                        # 匹配标的、方向
                        if (o["symbol"].startswith(clean_symbol) or clean_symbol in o["symbol"]) and "Buy" in str(o["side"]):
                            status_str = o["llm_status"].upper()
                            if any(s in status_str for s in ["PENDING", "NEW", "WAITING"]):
                                is_match = False
                                match_reason = ""
                                
                                # 类型相同且关键参数接近时拦截
                                if order_type in str(o.get("order_type")):
                                    if order_type == "LO" and price and o.get("price"):
                                        diff_pct = abs(float(price) - float(o["price"])) / float(o["price"])
                                        if diff_pct < 0.008:
                                            is_match = True
                                            match_reason = f"新旧买入限价差异仅为 {diff_pct*100:.2f}%，小于缓冲区 0.8%"
                                    elif order_type in ["LIT", "MIT"] and trigger_price and o.get("trigger_price"):
                                        diff_pct = abs(float(trigger_price) - float(o["trigger_price"])) / float(o["trigger_price"])
                                        if diff_pct < 0.008:
                                            is_match = True
                                            match_reason = f"新旧触发价差异仅为 {diff_pct*100:.2f}%，小于缓冲区 0.8%"
                                            
                                if is_match:
                                    logging.info(f"🚫 [Hysteresis] 拦截对 {symbol} 的重复买单。原因: {match_reason}。保持现有挂单 {o['order_id']}。")
                                    return json.dumps({
                                        "success": False,
                                        "order_id": o["order_id"],
                                        "symbol": cut_symbol(symbol),
                                        "message": f"拦截重复买单：当前已存在相似挂单，符合缓冲区策略({match_reason})。指令未执行，以节省 API 频率。"
                                    }, ensure_ascii=False)

                except Exception as hyst_err:
                    logging.warning(f"[Hysteresis] BuyStockTool 检查重复订单时出错: {hyst_err}")

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
            try:
                from tools.market_data import GetTechnicalAnalysisTool
                ta_tool = GetTechnicalAnalysisTool()
                ta_data_str = ta_tool.execute(symbol=cut_symbol(symbol))
                ta_snapshot = json.loads(ta_data_str)
            except Exception as e:
                logging.warning(f"Failed to capture TA snapshot for logging: {e}")
                ta_snapshot = None

            trade_logger.log_trade(
                symbol=cut_symbol(symbol),
                side="Buy",
                quantity=quantity,
                price=price,
                order_type=order_type,
                order_id=resp["order_id"],
                reason=reason,
                risk_score=risk_score,
                indicators=ta_snapshot,
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
                "message": f"买入指令下达成功 [{status_msg}]: {quantity}股 {symbol}。理由: {reason}。{execution_hint}"
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e), "success": False}, ensure_ascii=False)


class SellStockTool(BaseTool):
    """卖出股票或期权工具"""

    name = "sell_stock"
    description = "卖出股票或期权合约（平仓）。支持多种订单类型。内设高胜率领涨股(Tier 1 Leader)利润奔跑模式。"
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
            name="conviction",
            type="string",
            description="信心等级：'normal'(默认) 或 'high'。设置为 'high' 将标记为 Tier 1 Leader，自动应用更宽容的追踪止损和延迟的利润锁利算法，给翻倍牛股更多呼吸空间。",
            required=False,
            enum=["normal", "high"],
            default="normal"
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
        conviction: str = "normal",
        reason: str = "",
        **kwargs
    ) -> str:
        try:
            engine = get_trading_engine()

            full_symbol = modify_symbol(symbol)
            clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol

            # 安全校验：获取真实持仓
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
                if cost_price is not None:
                    try:
                        from tools.market_data import get_quote_ctx
                        ctx = get_quote_ctx()
                        quotes = ctx.quote([full_symbol])
                        if quotes:
                            current_price = float(quotes[0].last_done)
                            if current_price > cost_price:
                                # High Conviction 下 PRR 锁利倍率从 50% 放宽到 30%（即允许回撤 70% 的利润），给予更大波动空间
                                prr_retention_ratio = 0.3 if conviction == "high" else 0.5
                                # 锁定目标追踪比例
                                t_target = (prr_retention_ratio * (current_price - cost_price) / current_price) * 100.0
                                
                                # High Conviction 启动阈值从 3.0% 提高到 8.0%（即盈利覆盖掉 8% 波动才收紧），给予翻倍股前期足够空间
                                prr_trigger_threshold = 8.0 if conviction == "high" else 3.0
                                
                                if t_target >= prr_trigger_threshold and t_target < trailing_percent:
                                    orig_trailing_percent = trailing_percent
                                    trailing_percent = round(t_target, 2)
                                    logging.info(
                                        f"🛡️ [PRR Guard] 侦测到持仓 {symbol} 处于盈利状态 ({conviction.upper()} Conviction)。"
                                        f"动态利润留存锁启动，追踪比例由 {orig_trailing_percent}% 收网收紧至 {trailing_percent}%！"
                                    )
                    except Exception as prr_err:
                        logging.warning(f"[PRR Guard] 利润保护计算时发生异常: {prr_err}")
                
                orig_trailing_percent = trailing_percent
                # High Conviction 下上限拓宽至 35%，允许极端波动
                max_trailing = 35.0 if conviction == "high" else 25.0
                if trailing_percent > max_trailing:
                    trailing_percent = max_trailing
                    logging.warning(f"⚠️ [Sanity Check] 追踪止损百分比过宽 ({orig_trailing_percent}%)，自动缩限为 {max_trailing}%。")
                elif trailing_percent < 3.0:
                    trailing_percent = 3.0
                    logging.warning(f"⚠️ [Sanity Check] 追踪止损百分比过窄 ({orig_trailing_percent}%)，自动放大到 3.0%。")

            # 🚨 核心增强：引入调价缓冲区 (Hysteresis) 与重复订单拦截 (防止高频过度交易)
            if order_type in ["TSMPCT", "TSM", "LIT", "MIT"]:
                try:
                    today_orders = engine.get_today_orders()
                    # 提前获取实时价用于隐含触发价计算
                    cur_price = None
                    
                    for o in today_orders:
                        # 匹配标的、方向（支持 LONGPORT_ENGINE 返回的各种侧描述）
                        if (o["symbol"].startswith(clean_symbol) or clean_symbol in o["symbol"]) and "Sell" in str(o["side"]):
                            # 只有处于 PENDING/WAITING 状态的订单才需要对比
                            status_str = o["llm_status"].upper()
                            if any(s in status_str for s in ["PENDING", "NEW", "WAITING"]):
                                is_match = False
                                match_reason = ""
                                
                                # 1. 比例单对比 (🚨 修正：从 1.0% 升级为动态迟滞缓冲区，防范高波动股)
                                if order_type == "TSMPCT" and o.get("trailing_percent") is not None:
                                    diff = abs(float(trailing_percent) - float(o["trailing_percent"]))
                                    
                                    # 动态计算缓冲区阈值：max(1.5%, 0.5 * ATR_pct)
                                    # 如果拿不到 ATR，默认使用 1.5% 绝对值
                                    hysteresis_threshold = 1.5
                                    try:
                                        from tools.market_data import get_technical_analysis
                                        ta = get_technical_analysis(clean_symbol)
                                        atr_pct = float(ta.get("atr_pct", 3.0))
                                        hysteresis_threshold = max(1.5, atr_pct * 0.5)
                                    except:
                                        pass
                                        
                                    if diff < hysteresis_threshold:
                                        is_match = True
                                        match_reason = f"新旧追踪比例差异仅为 {diff:.2f}%，小于动态迟滞缓冲区阈值 {hysteresis_threshold:.2f}% (含 ATR 补偿)"
                                
                                # 2. 金额单对比 (🚨 修正：迟滞缓冲区从 1% 提高到 1.5% 相对值)
                                elif order_type == "TSM" and o.get("trailing_amount") is not None:
                                    diff_pct = abs(float(trailing_amount) - float(o["trailing_amount"])) / float(o["trailing_amount"])
                                    if diff_pct < 0.015:
                                        is_match = True
                                        match_reason = f"新旧追踪金额差异仅为 {diff_pct*100:.2f}%，小于迟滞缓冲区阈值 1.5%"

                                # 3. 触及单对比 (0.8% 相对值缓冲区)
                                elif order_type in ["LIT", "MIT"] and o.get("trigger_price") is not None:
                                    diff_pct = abs(float(trigger_price) - float(o["trigger_price"])) / float(o["trigger_price"])
                                    if diff_pct < 0.008:
                                        is_match = True
                                        match_reason = f"新旧触发价差异仅为 {diff_pct*100:.2f}%，小于迟滞缓冲区阈值 0.8%"

                                # 4. 跨类型模糊匹配 (防止在 TSMPCT 和 MIT 之间反复横跳)
                                if not is_match:
                                    cur_implied = None
                                    exi_implied = None
                                    if cur_price is None:
                                        from tools.market_data import get_quote_ctx
                                        q_res = get_quote_ctx().quote([modify_symbol(symbol)])
                                        if q_res: cur_price = float(q_res[0].last_done)
                                    
                                    if cur_price:
                                        # 计算新单隐含价
                                        if order_type == "TSMPCT" and trailing_percent:
                                            cur_implied = cur_price * (1 - float(trailing_percent)/100.0)
                                        elif order_type in ["MIT", "LIT"] and trigger_price:
                                            cur_implied = float(trigger_price)
                                            
                                        # 计算现有单隐含价
                                        o_type_str = str(o.get("order_type"))
                                        if "TSLP" in o_type_str and o.get("trailing_percent"):
                                            exi_implied = cur_price * (1 - float(o["trailing_percent"])/100.0)
                                        elif any(t in o_type_str for t in ["MIT", "LIT"]) and o.get("trigger_price"):
                                            exi_implied = float(o["trigger_price"])
                                            
                                        if cur_implied and exi_implied:
                                            diff_pct = abs(cur_implied - exi_implied) / exi_implied
                                            if diff_pct < 0.01: # 跨类型给予 1% 缓冲区
                                                is_match = True
                                                match_reason = f"跨止损类型(新:{order_type} vs 旧:{o_type_str})隐含触发价差异仅为 {diff_pct*100:.2f}%，小于 1% 缓冲区"

                                if is_match:
                                    logging.info(f"🚫 [Hysteresis] 拦截对 {symbol} 的高频无效微调。原因: {match_reason}。保持现有挂单 {o['order_id']}。")
                                    return json.dumps({
                                        "success": False,
                                        "order_id": o["order_id"],
                                        "symbol": cut_symbol(full_symbol),
                                        "message": f"拦截无效微调：当前已存在相似挂单，符合迟滞缓冲区策略({match_reason})。指令未执行，以节省 API 频率。若需强制修改，请先手动撤单或等待显著价差出现。"
                                    }, ensure_ascii=False)

                except Exception as hyst_err:
                    logging.warning(f"[Hysteresis] 检查重复订单时出错: {hyst_err}")

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
            
            try:
                from tools.market_data import GetTechnicalAnalysisTool
                ta_tool = GetTechnicalAnalysisTool()
                ta_data_str = ta_tool.execute(symbol=cut_symbol(full_symbol))
                ta_snapshot = json.loads(ta_data_str)
            except Exception as e:
                logging.warning(f"Failed to capture TA snapshot for logging: {e}")
                ta_snapshot = None

            trade_logger.log_trade(
                symbol=cut_symbol(full_symbol),
                side="Sell",
                quantity=quantity,
                price=price,
                order_type=order_type,
                order_id=resp["order_id"],
                reason=reason,
                risk_score=risk_score,
                indicators=ta_snapshot,
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
        from tools.engines import get_trading_engine
        import logging
        
        logging.info("⚙️ [Alignment] 启动追踪止损数量自动重整/对齐校验...")
        engine = get_trading_engine()
        
        # 1. 获取最新持仓 (已通过引擎层统一为股数)
        positions_data = engine.get_positions()
        pos_map = {p["symbol"].split('.')[0]: float(p["quantity"]) for p in positions_data if float(p["quantity"]) > 0}
        
        if not pos_map:
            logging.info("⚙️ [Alignment] 当前无持仓，无需对齐。")
            return "No positions found."
            
        # 2. 获取所有的今日订单 (已通过引擎层统一为股数)
        orders = engine.get_today_orders()
        
        # 3. 找出所有活跃的（PENDING_CONDITIONAL 等）追踪比例止损单
        pending_stops = {}
        for o in orders:
            status_str = o["llm_status"].lower()
            # 兼容 LLM 状态包装
            is_pending = any(s in status_str for s in ["pending", "new", "waiting"])
            is_sell = o["side"] == "Sell" or "Sell" in o["side"]
            is_tslppct = "TSLPPCT" in o["order_type"]
            
            if is_pending and is_sell and is_tslppct:
                sym = o["symbol"].split('.')[0]
                raw_order = o["raw_order"]
                pending_stops[sym] = {
                    "order_id": o["order_id"],
                    "quantity": float(o["quantity"]),
                    "trailing_percent": float(getattr(raw_order, 'trailing_percent', 8.0)),
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
                
                # 由于浮点数精度，使用差值判断
                if abs(actual_qty - stop_qty) > 0.1:
                    logging.warning(
                        f"🚨 [Alignment] 侦测到持仓数量与止损挂单数量不一致! "
                        f"标的: {sym}, 实际持仓: {actual_qty}股, 止损挂单: {stop_qty}股。"
                    )
                    
                    # 1) 撤销该旧单
                    try:
                        logging.info(f"⏳ [Alignment] 正在撤销旧的错配止损单: {stop['order_id']}...")
                        engine.cancel_order(stop["order_id"])
                        import time
                        time.sleep(0.5)  # 短暂休眠等额度释放
                    except Exception as ex:
                        logging.error(f"❌ [Alignment] 撤销旧止损单失败: {ex}")
                        continue
                        
                    # 2) 重新以最新全额持仓股数挂设
                    try:
                        trailing_pct = stop["trailing_percent"]
                        
                        resp = engine.submit_order(
                            symbol=sym,
                            side="Sell",
                            order_type="TSMPCT",
                            quantity=actual_qty,
                            trailing_percent=trailing_pct,
                            reason=f"[Auto-Alignment] 自动对齐全额持仓止损 ({trailing_pct}%)"
                        )
                        
                        logging.info(
                            f"✅ [Alignment] {sym} 追踪止损重整提交成功！"
                            f"股数: {actual_qty}股, 止损比例: {trailing_pct}%, 新订单ID: {resp['order_id']}"
                        )
                        realigned_count += 1
                    except Exception as sub_ex:
                        logging.error(f"❌ [Alignment] 重新下达止损挂单失败: {sub_ex}")
        
        logging.info(f"⚙️ [Alignment] 自动校验校验结束。共重整对齐了 {realigned_count} 个标的。")
        return f"Successfully realigned {realigned_count} stops."
        
    except Exception as e:
        import traceback
        logging.error(f"❌ [Alignment] 自动对齐异常: {e}, 堆栈: {traceback.format_exc()}")
        return f"Error: {e}"
