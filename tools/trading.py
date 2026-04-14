"""
交易工具集
封装LongPort OpenAPI的交易功能
"""
import json
from datetime import datetime, timedelta, time
from decimal import Decimal
from typing import Optional

import pytz
import holidays
from longport.openapi import (
    Config, QuoteContext, TradeContext,
    OrderSide, OrderType, TimeInForceType
)

from tools.base import BaseTool, ToolParameter
from data.trade_logger import get_trade_logger


def get_longport_config() -> Config:
    """获取LongPort配置"""
    return Config.from_env()


def cut_symbol(symbol: str) -> str:
    """移除股票代码后缀"""
    if len(symbol) >= 3 and symbol[-3:] == '.US':
        return symbol[:-3]
    return symbol


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
            return json.dumps({"error": str(e)})


class GetPositionsTool(BaseTool):
    """获取持仓工具"""

    name = "get_positions"
    description = "获取当前账户的股票持仓信息，包括股票代码、持仓数量、成本价等"
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)

            position_resp = trade.stock_positions()
            positions = []

            for channel in position_resp.channels:
                for stock in channel.positions:
                    if stock.currency == 'USD':
                        positions.append({
                            "symbol": cut_symbol(stock.symbol),
                            "quantity": str(stock.available_quantity),
                            "cost_price": str(stock.cost_price),
                            "currency": stock.currency,
                            "market_value": str(stock.market_value) if hasattr(stock, 'market_value') else "N/A"
                        })

            return json.dumps({
                "positions": positions,
                "count": len(positions)
            })

        except Exception as e:
            return json.dumps({"error": str(e)})


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
            return json.dumps({"error": str(e)})


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
                        "status": str(o.status),
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
            return json.dumps({"error": str(e)})


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
                        "status": str(o.status),
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
            return json.dumps({"error": str(e)})


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
            return json.dumps({"error": str(e)})


class BuyStockTool(BaseTool):
    """买入股票工具"""

    name = "buy_stock"
    description = "买入股票，支持市价单(MO)和限价单(LO)"
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
            description="订单类型: MO(市价单) 或 LO(限价单)",
            enum=["MO", "LO"]
        ),
        ToolParameter(
            name="price",
            type="number",
            description="限价单价格（仅限价单需要）",
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
        reason: str = "",
        **kwargs
    ) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)

            full_symbol = modify_symbol(symbol)

            order_params = {
                "side": OrderSide.Buy,
                "symbol": full_symbol,
                "submitted_quantity": Decimal(str(quantity)),
                "time_in_force": TimeInForceType.Day,
                "remark": reason[:100] if reason else "AI Agent Buy Order"
            }

            if order_type == "LO":
                if price is None:
                    return json.dumps({"error": "限价单必须指定价格"})
                order_params["order_type"] = OrderType.LO
                order_params["submitted_price"] = Decimal(str(price))
            else:
                order_params["order_type"] = OrderType.MO

            # ── 影子实盘资金校验 ──
            try:
                # 获取可用现金
                balances = trade.account_balance('USD')
                available_cash = 0.0
                for b in balances:
                    available_cash = float(b.total_cash)
                    break
                
                # 估算成本
                quote_ctx = QuoteContext(config)
                q_res = quote_ctx.quote([full_symbol])
                curr_price = float(q_res[0].last_done) if q_res else (price or 0.0)
                estimated_cost = curr_price * quantity
                
                if available_cash <= 0:
                    return json.dumps({
                        "success": False, 
                        "error": f"账户现金余额为负 (${available_cash:.2f})，存在融资欠款。影子模式下禁止买入以模拟真实风控。"
                    }, ensure_ascii=False)
                
                if estimated_cost > available_cash:
                     return json.dumps({
                        "success": False, 
                        "error": f"可用现金不足。预计需 ${estimated_cost:.2f}，可用现金 ${available_cash:.2f}。"
                    }, ensure_ascii=False)
            except Exception as fund_err:
                # 记录但不中断，防止API波动导致无法测试
                print(f"Shadow fund check warning: {fund_err}")

            # ── 影子实盘逻辑 ──
            # resp = trade.submit_order(**order_params)
            mock_order_id = f"MOCK-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # 记录交易日志 (标记为模拟)
            trade_logger = get_trade_logger()
            latest_risk = trade_logger.get_latest_risk_score()
            risk_score = latest_risk["score"] if latest_risk else 0
            trade_logger.log_trade(
                symbol=cut_symbol(full_symbol),
                side="Buy",
                quantity=quantity,
                price=price or 0.0,
                order_type=order_type,
                order_id=mock_order_id,
                reason=f"[SHADOW MODE] {reason}",
                risk_score=risk_score,
            )

            return json.dumps({
                "success": True,
                "order_id": mock_order_id,
                "symbol": cut_symbol(full_symbol),
                "side": "Buy",
                "quantity": quantity,
                "order_type": order_type,
                "price": price if order_type == "LO" else "市价",
                "message": f"【实盘测试】模拟买入订单已记录: {quantity}股 {symbol}"
            })

        except Exception as e:
            return json.dumps({"error": str(e), "success": False})


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
            description="订单类型: MO(市价单) 或 LO(限价单)",
            enum=["MO", "LO"]
        ),
        ToolParameter(
            name="price",
            type="number",
            description="限价单价格（仅限价单需要）",
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
        reason: str = "",
        **kwargs
    ) -> str:
        try:
            config = get_longport_config()
            trade = TradeContext(config)

            full_symbol = modify_symbol(symbol)

            order_params = {
                "side": OrderSide.Sell,
                "symbol": full_symbol,
                "submitted_quantity": Decimal(str(quantity)),
                "time_in_force": TimeInForceType.Day,
                "remark": reason[:100] if reason else "AI Agent Sell Order"
            }

            if order_type == "LO":
                if price is None:
                    return json.dumps({"error": "限价单必须指定价格"})
                order_params["order_type"] = OrderType.LO
                order_params["submitted_price"] = Decimal(str(price))
            else:
                order_params["order_type"] = OrderType.MO

            # ── 影子实盘逻辑 ──
            # resp = trade.submit_order(**order_params)
            mock_order_id = f"MOCK-SELL-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # 记录交易日志 (标记为模拟)
            trade_logger = get_trade_logger()
            latest_risk = trade_logger.get_latest_risk_score()
            risk_score = latest_risk["score"] if latest_risk else 0
            trade_logger.log_trade(
                symbol=cut_symbol(full_symbol),
                side="Sell",
                quantity=quantity,
                price=price or 0.0,
                order_type=order_type,
                order_id=mock_order_id,
                reason=f"[SHADOW MODE] {reason}",
                risk_score=risk_score,
            )

            return json.dumps({
                "success": True,
                "order_id": mock_order_id,
                "symbol": cut_symbol(full_symbol),
                "side": "Sell",
                "quantity": quantity,
                "order_type": order_type,
                "price": price if order_type == "LO" else "市价",
                "message": f"【实盘测试】模拟卖出订单已记录: {quantity}股 {symbol}"
            })

        except Exception as e:
            return json.dumps({"error": str(e), "success": False})


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
        GetQuoteTool(),
        BuyStockTool(),
        SellStockTool(),
    ]
