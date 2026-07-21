# tools/engines/longport_engine.py
import logging
import time
from decimal import Decimal
from typing import Optional, Dict, Any, List

from longport.openapi import (
    Config, TradeContext, OrderSide, OrderType, TimeInForceType, OutsideRTH
)

from tools.engines.base import BaseTradingEngine

# 统一获取 LongPort 配置
def _get_config() -> Config:
    return Config.from_env()

class LongPortTradingEngine(BaseTradingEngine):
    """
    LongPort 长桥证券具体的交易适配器实现
    """

    def __init__(self):
        self._config = _get_config()
        # 创建底层的 TradeContext 实例
        self._trade_ctx = TradeContext(self._config)
        self._mock_sales = {} # Maps symbol to net mock quantity change
        logging.info("🎯 LongPortTradingEngine 底层 TradeContext 链接初始化完成")

    def _calculate_dynamic_slippage(self, symbol: str, base_price: float, order_side: str, order_type: str) -> tuple[float, float]:
        """
        核心升级：同时支持期权与正股的千股千面动态滑点计算
        """
        from tools.market_data import get_quote_ctx, modify_symbol
        
        base_price = float(base_price)
        
        # 1. 期权专属滑点策略
        if self.is_option_symbol(symbol):
            if base_price < 1.0:
                slippage_amt = 0.05  # 5美分宽容度
            elif base_price < 5.0:
                slippage_amt = 0.15  # 15美分宽容度
            else:
                slippage_amt = base_price * 0.05  # 5% 的滑点容错率
                
            limit_offset = max(slippage_amt * 2, 0.10)
            
            adjusted_price = base_price
            if order_side == "Buy":
                adjusted_price = base_price + slippage_amt
            else:
                adjusted_price = max(base_price - slippage_amt, 0.01)
                
            logging.info(f"🔮 [Slippage Option] {symbol} 原价 {base_price} -> 滑点后限价 {adjusted_price}, 容错偏移 (limit_offset) {limit_offset}")
            return round(adjusted_price, 2), round(limit_offset, 2)
            
        # 2. 正股原滑点策略
        try:
            quote_ctx = get_quote_ctx()
            quotes = quote_ctx.quote([modify_symbol(symbol)])
            current_price = float(quotes[0].last_done) if quotes else float(base_price)
        except Exception as e:
            logging.warning(f"获取正股现价计算滑点失败 ({e})，降级为使用 base_price 进行计算")
            current_price = float(base_price)

        if current_price > 500:
            slippage_pct = 0.001
        elif current_price > 50:
            slippage_pct = 0.002
        else:
            slippage_pct = 0.003

        slippage_amt = current_price * slippage_pct
        slippage_amt = max(slippage_amt, 0.02)
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

    def _cancel_duplicate_pending_orders(self, symbol: str, order_side: str) -> int:
        """
        自动检索并撤销同向的所有未成交挂单，防止新单下达时产生冲突，返回成功撤销的订单数量。
        """
        cancelled_count = 0
        try:
            orders = self._trade_ctx.today_orders()
            clean_symbol = symbol.split('.')[0]
            side_str = "Buy" if order_side == "Buy" else "Sell"

            for o in orders:
                # 匹配标的和方向
                if o.symbol.startswith(clean_symbol) and side_str in str(o.side):
                    status_str = str(o.status).lower()
                    # 检查挂单状态
                    if any(s in status_str for s in ["notreported", "new", "submitted", "pending", "partialfilled"]):
                        logging.info(f"🚨 [Cancel-Before-Modify] 发现同向冲突未成交订单 {o.order_id}，正在自动秒级下达撤单指令...")
                        self._trade_ctx.cancel_order(o.order_id)
                        cancelled_count += 1
            if cancelled_count > 0:
                logging.info(f"⏳ [Cancel-Before-Modify] 已成功发送 {cancelled_count} 个冲突订单的撤单指令，短暂休眠 0.5s 等对冲额度释放...")
                time.sleep(0.5)
        except Exception as e:
            logging.warning(f"[Cancel-Before-Modify] 自动撤销同向挂单时发生异常: {e}")
        return cancelled_count

    def _parse_option_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        解析 OCC 标准美股期权符号。
        例如: TSM260619P00150000.US -> underlying: TSM, expiry: 260619, option_type: Put, strike: 150.0
        """
        import re
        clean_sym = symbol.split('.')[0] if '.' in symbol else symbol
        # 兼容 4 至 8 位的行权价数字表示形式
        pattern = r'^([A-Z]{1,6})([0-9]{6})([CP])([0-9]{4,8})$'
        match = re.match(pattern, clean_sym)
        if match:
            return {
                "underlying": match.group(1),
                "expiry": match.group(2),
                "option_type": "Call" if match.group(3) == "C" else "Put",
                "strike_price": float(match.group(4)) / 1000.0
            }
        return None

    def submit_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        trailing_percent: Optional[float] = None,
        trailing_amount: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        提交 LongPort 交易订单。支持：
        - 期权/股票的格式判定与自动数量乘数换算 (quantity/100)
        - 自动冲突挂单撤销 (Cancel-Before-Modify)
        - 自适应滑点与容错偏移 (limit_offset) 计算
        """
        from tools.market_data import modify_symbol
        
        full_symbol = modify_symbol(symbol)
        order_side = OrderSide.Buy if side == "Buy" else OrderSide.Sell
        
        # 1. 自动冲突挂单撤销 (Cancel-Before-Modify)
        if order_type != "MO":
            self._cancel_duplicate_pending_orders(symbol, side)

        # 2. 数量适配：核心修正 - 统一接收“股数”，若是期权代码，自动将股数转换为张数 (1张 = 100股)
        if self.is_option_symbol(full_symbol):
            # 采用 Decimal 确保精度，且至少为 1 张
            actual_qty = Decimal(str(max(int(round(quantity / 100.0)), 1)))
            logging.info(f"🔮 [Multiplier Conversion] 检测到期权交易 ({full_symbol})，自动将输入股数 {quantity} 转换为张数 {actual_qty} 张")
        else:
            actual_qty = Decimal(str(quantity))

        # ─── 物理级期权交易安全拦截网 (Security Interceptors) ───
        if self.is_option_symbol(full_symbol):
            opt_info = self._parse_option_symbol(full_symbol)
            if opt_info:
                # 1) 单笔最大期权张数拦截 (物理红线：单笔最大不得超过 5 张)
                MAX_OPTION_CONTRACTS_PER_ORDER = 5
                if actual_qty > MAX_OPTION_CONTRACTS_PER_ORDER:
                    logging.error(f"❌ [Risk Interceptor] 单笔期权下单张数 {actual_qty} 超过安全红线 ({MAX_OPTION_CONTRACTS_PER_ORDER} 张)，系统强行拒绝下单！")
                    raise ValueError(f"风控硬拦截: 单笔期权下单张数 ({actual_qty}张) 超过安全物理红线 ({MAX_OPTION_CONTRACTS_PER_ORDER}张)！")

                # 2) Covered Call（备兑看涨期权）安全性拦截
                if side == "Sell" and opt_info["option_type"] == "Call":
                    # 检查是否持有足额正股底仓（Sell Call 开仓需有足额正股，平仓由于原本就是买单故不走该逻辑，但这里为安全全额校验正股持仓）
                    positions = self.get_positions()
                    underlying_stock = f"{opt_info['underlying']}.US"
                    held_stock_qty = 0.0
                    for pos in positions:
                        if pos["symbol"] == underlying_stock:
                            held_stock_qty += pos["quantity"]
                    
                    required_stock_qty = float(actual_qty * 100)
                    if held_stock_qty < required_stock_qty:
                        error_msg = (
                            f"❌ [Risk Interceptor] 强行拦截裸 Sell Call！"
                            f"当前账户仅持有 {opt_info['underlying']} 正股 {held_stock_qty} 股，"
                            f"无法开仓卖出备兑 Call {actual_qty} 张 (需要 {required_stock_qty} 股正股担保)。"
                            f"裸 Sell Call 亏损无上限，物理层已硬性锁死并拒绝下单！"
                        )
                        logging.error(error_msg)
                        raise ValueError(error_msg)

                # 3) Cash-Secured Put（现金备兑看跌期权）资金硬保障拦截
                elif side == "Sell" and opt_info["option_type"] == "Put":
                    # 核心修正：区分“平仓多头”与“卖出开仓”
                    # 先检查是否持有该期权的多头头寸
                    positions = self.get_positions()
                    held_option_qty = 0.0
                    for pos in positions:
                        if pos["symbol"] == full_symbol:
                            held_option_qty += pos["quantity"]
                    
                    # 只有当卖出数量大于持仓数量（即涉及卖出开仓/做空期权）时，才需要校验保证金
                    if float(actual_qty * 100) > held_option_qty:
                        balance = self.get_account_balance()
                        buying_power = balance["buying_power"]
                        # 计算担保所需现金：行权价 * (卖出数量 - 已持仓数量) * 100
                        excess_qty = float(actual_qty) - (held_option_qty / 100.0)
                        required_margin = opt_info["strike_price"] * excess_qty * 100.0
                        
                        if buying_power < required_margin:
                            error_msg = (
                                f"❌ [Risk Interceptor] 现金备兑 Put 资金保障不足！"
                                f"你正尝试卖出开仓 {excess_qty} 张 {full_symbol}，"
                                f"需要现金担保 ${required_margin:.2f}，而当前账户可用购买力仅为 ${buying_power:.2f}！"
                                f"防止保证金爆仓，系统强行锁定并拒绝下单！"
                            )
                            logging.error(error_msg)
                            raise ValueError(error_msg)
                        else:
                            logging.info(f"✅ [Risk Interceptor] 卖出开仓 {excess_qty} 张 Put，保证金校验通过。")
                    else:
                        logging.info(f"✅ [Risk Interceptor] 检测到正在平仓现有期权多头 ({actual_qty} 张)，豁免保证金校验。")

                # 4) Protective Put（保护性看跌期权）正股配对比例拦截 (防止超额过度对冲)
                elif side == "Buy" and opt_info["option_type"] == "Put":
                    # 豁免大盘指数 (SPY, QQQ, IWM) 的 1:100 强配对要求，允许作为“全池对冲”救生圈
                    if opt_info["underlying"] in ["SPY", "QQQ", "IWM"]:
                        logging.info(f"🛡️ [Risk Interceptor] 检测到大盘指数期权 ({full_symbol}) 对冲，豁免 1:100 正股配对物理拦截，允许作为全池救生圈执行。")
                    else:
                        positions = self.get_positions()
                        underlying_stock = f"{opt_info['underlying']}.US"
                        held_stock_qty = 0.0
                        for pos in positions:
                            if pos["symbol"] == underlying_stock:
                                held_stock_qty += pos["quantity"]

                        required_stock_qty = float(actual_qty * 100)
                        if held_stock_qty < required_stock_qty:
                            error_msg = (
                                f"❌ [Risk Interceptor] 强行拦截保护性看跌期权 (Protective Put)！"
                                f"购买 {actual_qty} 张 Put 期权需要持仓至少持有 {required_stock_qty} 股 {opt_info['underlying']} 正股进行 1:100 配对，"
                                f"而当前实际持仓仅为 {held_stock_qty} 股。这会导致严重的超额过度对冲 (Over-hedged) 并吞噬利润！"
                                f"物理层已硬性锁死并拒绝下单！"
                            )
                            logging.error(error_msg)
                            raise ValueError(error_msg)

        # 3. 决定有效期类型 Time in Force
        tif = TimeInForceType.Day if order_type == "MO" else TimeInForceType.GoodTilCanceled

        # 4. 初始化下单通用参数
        order_params = {
            "side": order_side,
            "symbol": full_symbol,
            "submitted_quantity": actual_qty,
            "time_in_force": tif,
            "outside_rth": OutsideRTH.AnyTime,
            "remark": kwargs.get("reason", "AI Agent Order")[:100]
        }

        # 5. 根据具体订单类型进行价格和偏移量填充，并调用 _calculate_dynamic_slippage 计算滑点
        if order_type == "LO":
            if price is None:
                raise ValueError("限价单(LO)必须指定 price")
            adj_price, _ = self._calculate_dynamic_slippage(symbol, price, side, "LO")
            logging.info(f"动态滑点调整 (LO): 原价 {price} -> 滑后限价 {adj_price}")
            order_params["order_type"] = OrderType.LO
            order_params["submitted_price"] = Decimal(str(adj_price))
            
        elif order_type == "LIT":
            if trigger_price is None:
                raise ValueError("触及限价单(LIT)必须指定 trigger_price")
            adj_price, _ = self._calculate_dynamic_slippage(symbol, trigger_price, side, "LIT")
            logging.info(f"动态滑点调整 (LIT): 触发价 {trigger_price} -> 限价 {adj_price}")
            order_params["order_type"] = OrderType.LIT
            order_params["submitted_price"] = Decimal(str(adj_price))
            order_params["trigger_price"] = Decimal(str(trigger_price))
            
        elif order_type == "MIT":
            if trigger_price is None:
                raise ValueError("触及市价单(MIT)必须指定 trigger_price")
            order_params["order_type"] = OrderType.MIT
            order_params["trigger_price"] = Decimal(str(trigger_price))
            
        elif order_type == "TSMPCT":
            if trailing_percent is None:
                raise ValueError("追踪止损百分比单(TSMPCT)必须指定 trailing_percent")
            order_params["order_type"] = OrderType.TSLPPCT
            order_params["trailing_percent"] = Decimal(str(trailing_percent))
            _, dynamic_offset = self._calculate_dynamic_slippage(symbol, 100, side, "TSM")
            order_params["limit_offset"] = Decimal(str(dynamic_offset))
            
        elif order_type == "TSM":
            if trailing_amount is None:
                raise ValueError("追踪止损金额单(TSM)必须指定 trailing_amount")
            order_params["order_type"] = OrderType.TSLPAMT
            order_params["trailing_amount"] = Decimal(str(trailing_amount))
            _, dynamic_offset = self._calculate_dynamic_slippage(symbol, 100, side, "TSM")
            order_params["limit_offset"] = Decimal(str(dynamic_offset))
            
        else:
            order_params["order_type"] = OrderType.MO

        # 6. 提交至长桥 API (支持 DRY_RUN 沙盒模拟阻断)
        import os
        if os.getenv("TRADING_DRY_RUN", "false").lower() == "true":
            logging.info(f"🚧 [DRY_RUN MOCK] 检测到开启了交易 DRY_RUN 沙盒，跳过真实长桥下单。组装好的下单报文细节: {order_params}")
            qty_val = float(actual_qty)
            if side == "Sell" or side == "OrderSide.Sell":
                self._mock_sales[symbol] = self._mock_sales.get(symbol, 0.0) - qty_val
                logging.info(f"🚧 [DRY_RUN MOCK] 虚拟扣减持仓: {symbol} 减少 {qty_val} 股/张 (当前累计变化: {self._mock_sales[symbol]})")
            elif side == "Buy" or side == "OrderSide.Buy":
                self._mock_sales[symbol] = self._mock_sales.get(symbol, 0.0) + qty_val
                logging.info(f"🚧 [DRY_RUN MOCK] 虚拟增加持仓: {symbol} 增加 {qty_val} 股/张 (当前累计变化: {self._mock_sales[symbol]})")
            return {
                "order_id": f"DRY-RUN-MOCK-{int(time.time())}",
                "success": True,
                "raw_response": "DRY_RUN_MOCK_RESP"
            }

        resp = self._trade_ctx.submit_order(**order_params)
        
        # 7. 返回标准规范化的响应字典
        return {
            "order_id": resp.order_id,
            "success": True,
            "raw_response": resp
        }

    def cancel_order(self, order_id: str) -> bool:
        """撤销挂单"""
        try:
            self._trade_ctx.cancel_order(order_id)
            return True
        except Exception as e:
            logging.error(f"LongPort 撤单失败: {e}")
            return False

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        获取当前账户持仓，并进行标准格式清洗，支持 Dry-Run 模拟仓位增减。
        核心修正：对于期权，将底层返回的“张数”自动乘以 100 转换为“股数”，确保与主系统单位对齐。
        """
        position_resp = self._trade_ctx.stock_positions()
        cleaned = []
        retrieved_symbols = set()
        
        for channel in position_resp.channels:
            for pos in channel.positions:
                symbol = pos.symbol
                retrieved_symbols.add(symbol)
                is_opt = self.is_option_symbol(symbol)
                asset_type = "OPTION" if is_opt else "STOCK"
                
                # 核心单位转换逻辑
                qty = float(pos.available_quantity)
                if is_opt:
                    qty = qty * 100.0  # 张转股
                
                import os
                if os.getenv("TRADING_DRY_RUN", "false").lower() == "true":
                    mock_change = self._mock_sales.get(symbol, 0.0)
                    # mock_change 存储的是 actual_qty (张数)，也需要转换
                    if is_opt:
                        qty += mock_change * 100.0
                    else:
                        qty += mock_change
                
                if qty > 0:
                    cleaned.append({
                        "symbol": symbol,
                        "quantity": qty,
                        "cost_price": float(pos.cost_price),
                        "market_value": float(pos.market_value) if hasattr(pos, 'market_value') else 0.0,
                        "asset_type": asset_type,
                        "raw_position": pos
                    })
                    
        # 兼容在沙盒中买入、且实盘没有持仓的股票
        import os
        if os.getenv("TRADING_DRY_RUN", "false").lower() == "true":
            for symbol, mock_change in self._mock_sales.items():
                if symbol not in retrieved_symbols and mock_change > 0:
                    is_opt = self.is_option_symbol(symbol)
                    asset_type = "OPTION" if is_opt else "STOCK"
                    qty = mock_change * 100.0 if is_opt else mock_change
                    cleaned.append({
                        "symbol": symbol,
                        "quantity": qty,
                        "cost_price": 0.0,
                        "market_value": 0.0,
                        "asset_type": asset_type,
                        "raw_position": None
                    })
                    
        return cleaned

    def get_account_balance(self) -> Dict[str, Any]:
        """
        获取账户资金余额、可用购买力与总资产。
        🚨 升级：实现多币种自动汇总 (Consolidated Balance)。
        由于用户可能持有 HKD/SGD 闲置资金但 USD 账户为负，导致系统误判为融资。
        本逻辑会自动将所有币种余额按实时/预设汇率折算为 USD，提供真实的“全局现金”视图。
        """
        # 获取所有币种的余额信息
        acc_infos = self._trade_ctx.account_balance()
        if not acc_infos:
            raise RuntimeError("无法获取 LongPort 账户余额数据")
            
        # 预设基础汇率（LongPort API 响应中通常不直接提供实时汇率，此处使用保守估计或从 QuoteContext 获取）
        # 实际生产中建议通过 quote_ctx.realtime_quotes(['USDHKD', 'USDSGD']) 获取
        EXCHANGE_RATES = {
            "USD": 1.0,
            "HKD": 0.128,  # 1 HKD ~ 0.128 USD
            "SGD": 0.745,  # 1 SGD ~ 0.745 USD
            "CNH": 0.138,
            "JPY": 0.0065
        }
        
        total_cash_usd = 0.0
        total_buying_power_usd = 0.0
        total_net_assets_usd = 0.0
        
        for acc in acc_infos:
            currency = acc.currency
            rate = EXCHANGE_RATES.get(currency, 1.0)
            
            # 使用 total_cash (包含结算中现金) 计算全局水位
            total_cash_usd += float(acc.total_cash) * rate
            total_buying_power_usd += float(acc.buy_power) * rate
            total_net_assets_usd += float(acc.net_assets) * rate
            
        logging.info(f"💰 [Consolidation] 多币种汇总完成: 全局现金 ${total_cash_usd:,.2f} USD, 全局购买力 ${total_buying_power_usd:,.2f} USD")
        
        return {
            "cash": total_cash_usd,
            "buying_power": total_buying_power_usd,
            "net_assets": total_net_assets_usd,
            "raw_balance": acc_infos
        }

    def get_today_orders(self) -> List[Dict[str, Any]]:
        """获取今日订单，并转换订单状态，使其易于大模型认知"""
        orders = self._trade_ctx.today_orders()
        cleaned = []
        for o in orders:
            # 状态包装
            status_str = str(o.status)
            llm_status = self._format_order_status_for_llm(status_str)
            
            is_opt = self.is_option_symbol(o.symbol)
            qty = float(getattr(o, 'quantity', 0.0))
            exec_qty = float(getattr(o, 'executed_quantity', 0.0))
            if is_opt:
                qty = qty * 100.0  # 张转股
                exec_qty = exec_qty * 100.0
            
            # 核心增强：提取追踪止损和触及单的关键参数，消除 CIO 的认知盲区
            trailing_percent = float(o.trailing_percent) if hasattr(o, 'trailing_percent') and o.trailing_percent else None
            trailing_amount = float(o.trailing_amount) if hasattr(o, 'trailing_amount') and o.trailing_amount else None
            trigger_price = float(o.trigger_price) if hasattr(o, 'trigger_price') and o.trigger_price else None
            limit_offset = float(o.limit_offset) if hasattr(o, 'limit_offset') and o.limit_offset else None

            cleaned.append({
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": str(o.side),
                "order_type": str(o.order_type),
                "quantity": qty,
                "executed_quantity": exec_qty,
                "price": float(getattr(o, 'price', 0.0)) if getattr(o, 'price', None) and float(o.price) > 0 else None,
                "trailing_percent": trailing_percent,
                "trailing_amount": trailing_amount,
                "trigger_price": trigger_price,
                "limit_offset": limit_offset,
                "status": status_str,
                "llm_status": llm_status,
                "raw_order": o
            })
        return cleaned

    def get_history_orders(self, days: int) -> List[Dict[str, Any]]:
        """获取最近 days 天的历史订单记录"""
        from datetime import datetime, timedelta
        
        orders = self._trade_ctx.history_orders(
            start_at=datetime.now() - timedelta(days=days),
            end_at=datetime.now() + timedelta(days=1)
        )
        cleaned = []
        for o in orders:
            status_str = str(o.status)
            llm_status = self._format_order_status_for_llm(status_str)
            
            is_opt = self.is_option_symbol(o.symbol)
            qty = float(getattr(o, 'quantity', 0.0))
            exec_qty = float(getattr(o, 'executed_quantity', 0.0))
            if is_opt:
                qty = qty * 100.0  # 张转股
                exec_qty = exec_qty * 100.0
                
            # 核心增强：提取追踪止损和触及单的关键参数
            trailing_percent = float(o.trailing_percent) if hasattr(o, 'trailing_percent') and o.trailing_percent else None
            trailing_amount = float(o.trailing_amount) if hasattr(o, 'trailing_amount') and o.trailing_amount else None
            trigger_price = float(o.trigger_price) if hasattr(o, 'trigger_price') and o.trigger_price else None
            limit_offset = float(o.limit_offset) if hasattr(o, 'limit_offset') and o.limit_offset else None

            cleaned.append({
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": str(o.side),
                "order_type": str(o.order_type),
                "quantity": qty,
                "executed_quantity": exec_qty,
                "price": float(getattr(o, 'price', 0.0)) if getattr(o, 'price', None) and float(o.price) > 0 else None,
                "trailing_percent": trailing_percent,
                "trailing_amount": trailing_amount,
                "trigger_price": trigger_price,
                "limit_offset": limit_offset,
                "status": status_str,
                "llm_status": llm_status,
                "raw_order": o,
                "submitted_at": o.submitted_at
            })
        return cleaned

    def _format_order_status_for_llm(self, status_str: str) -> str:
        """
        格式化晦涩的长桥订单状态说明
        """
        if "VarietiesNotReported" in status_str:
            return f"{status_str} (PENDING_CONDITIONAL)"
        elif "NotReported" in status_str:
            return f"{status_str} (PENDING_SUBMITTED)"
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
