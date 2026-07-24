import json
import logging
from typing import Dict, List, Any
from datetime import datetime
import pytz

from data.trade_logger import get_trade_logger

logger = logging.getLogger("strategic_agent")

class PortfolioManager:
    """
    高级投资组合管理器 (Portfolio Manager)
    负责从宏观账户层面进行自我进化和资金管理：
    1. 现金流与阈值自适应 (Cash Drag & Adaptive Thresholds)
    2. 板块权重分布与相关性惩罚 (Sector Exposure & Penalties)
    3. 优胜劣汰算法 (Weed & Flower - 相对强度排序)
    """
    
    def __init__(self):
        self.trade_logger = get_trade_logger()
        
    def analyze_portfolio(self, current_positions: List[Dict], account_balance: Dict, macro_risk: Dict, sector_briefing: Dict = None) -> Dict:
        """
        进行组合级扫描，输出全局调仓指令和买入阈值
        """
        logger.info("[Portfolio Manager] 开始投资组合级别审查...")
        
        # 提取宏观风控评分与状态
        score = float(macro_risk.get("score", 50.0))
        regime = macro_risk.get("regime", "normal")
        
        # 连续性线性插值，杜绝死板硬编码数字，做到“千股千面、状态自适应”
        # 1. 单股持仓上限：从 score=0 时的 5.0% 线性过渡到 score=100 时的 15.0%
        max_stock_limit = round(5.0 + (score / 100.0) * 10.0, 2)
        
        # 2. 板块持仓上限：从 score=0 时的 15.0% 线性过渡到 score=100 时的 45.0%
        max_sector_limit = round(15.0 + (score / 100.0) * 30.0, 2)
        
        # 3. 建议的 ATR 追踪止损乘数：从 score=0 时的 1.5x 线性过渡到 score=100 时的 4.0x
        # 熊市 (score < 50) 紧防守 (1.5x ~ 2.5x ATR)，牛市 (score >= 70) 宽容度大 (3.0x ~ 4.0x ATR) 让利润奔跑
        recommended_atr_multiplier = round(1.5 + (score / 100.0) * 2.5, 2)
        
        # 4. 保本平价单（Break-even Stop）策略：在震荡市或熊市 (score < 60) 中强制启动
        # 当个股浮盈达到 1.0 * ATR_pct (一般个股约 2.5% ~ 3.5%) 时，系统必须提拉止损线至成本线，保本锁死风险。
        is_breakeven_enforced = score < 60.0
        
        directives = {
            "adaptive_buy_threshold": 50, # 默认买入风控阈值
            "portfolio_health": "Neutral",
            "macro_risk_score": score,
            "macro_regime": regime,
            "dynamic_limits": {
                "max_single_stock_exposure_pct": max_stock_limit,
                "max_single_sector_exposure_pct": max_sector_limit,
                "recommended_atr_trailing_multiplier": recommended_atr_multiplier,
                "tier1_min_atr_multiplier": 4.0, # 🚨 核心补丁：Tier 1 领涨股强制 4.0x ATR 起步，防 Whipsaw
                "is_breakeven_enforced_under_low_score": is_breakeven_enforced,
                "breakeven_trigger_atr_multiplier": 1.0,
                "description": f"当前处于 {regime.upper()} 状态 (score={score})。根据此状态，系统已启用自适应风控红线：单股持仓上限 {max_stock_limit}%，板块持仓上限 {max_sector_limit}%，追踪止损推荐使用 {recommended_atr_multiplier}x ATR。Tier 1 领涨股强制开启 4.0x ATR 宽容防线。保本平价单强制开启状态: {is_breakeven_enforced}。"
            },
            "sector_limits": {},          # 板块限制
            "weed_out_list": [],          # 建议主动淘汰的弱势持仓
            "recently_weeded_out": [],    # 最近被淘汰的弱势持仓保护禁买名单
            "sector_flow_penalties": {},  # 🚨 升级 V4.6：板块资金流向惩罚（取代一票否决）
            "hedge_circuit_breaker": False, # 🚨 升级 V4.6：对冲熔断开关
            "main_line_bias": None,        # 🚨 升级 V4.6：主线偏好引导
        }
        
        # 1. 计算胜率自适应阈值
        directives["adaptive_buy_threshold"] = self._calculate_adaptive_threshold(macro_risk)
        
        # 2. 🚨 升级 V4.6：处理板块资金流向惩罚与主线引导
        if sector_briefing:
            weak_sectors = sector_briefing.get("weak_sectors", [])
            # 将“一票否决”改为“软性惩罚”：减半头寸
            directives["sector_flow_penalties"] = {s: 0.5 for s in weak_sectors}
            
            # 主线引导：如果是最强主线板块，给予 1.2x 仓位权重加成
            main_line = sector_briefing.get("market_main_line")
            if main_line and main_line != "Unknown":
                directives["main_line_bias"] = {
                    "sector": main_line,
                    "multiplier": 1.2,
                    "reason": f"当前市场主线明确为 {main_line}，允许在风险可控的前提下适度超配。"
                }
            
            if weak_sectors:
                logger.warning(f"[Portfolio Manager] ⚠️ 侦测到资金流出板块: {weak_sectors}。已激活减半仓位惩罚逻辑。")

        # 3. 🚨 升级 V4.6：对冲熔断逻辑 (Hedge Circuit Breaker)
        # 如果 crash_detection 分值极高 (100) 且市场温度极高 (100)，说明大盘正在暴力反弹
        crash_score = macro_risk.get("components", {}).get("crash_detection", 100)
        market_temp = macro_risk.get("components", {}).get("market_temperature", 50)
        
        if crash_score >= 95 and market_temp >= 90:
            directives["hedge_circuit_breaker"] = True
            logger.info("[Portfolio Manager] 🔥 检测到暴力大反弹信号，已激活对冲熔断建议 (Hedge Circuit Breaker)。")

        # 4. 计算最近被淘汰/硬止损的持仓保护名单 (Weed-out Cooldown Logic)
        # 🚨 修正：硬止损(Hard Stop) 3天冷静期（物理锁死），防 Whipsaw。
        # 🚨 新增：日内 4 小时绝对冷静期（物理锁死），即便开启 force_recovery 也要观察 4h。
        recently_weeded = []
        try:
            recent_logs = self.trade_logger.get_recent_logs(days=5)
            now = datetime.now(pytz.timezone("US/Eastern"))
            now_unix = int(now.timestamp())
            
            for daily in recent_logs:
                log_date_str = daily.get("date")
                log_date = datetime.strptime(log_date_str, "%Y-%m-%d").date()
                days_ago = (now.date() - log_date).days
                
                for t in daily.get("trades", []):
                    reason = str(t.get("reason", "")).lower()
                    side = str(t.get("side", "")).lower()
                    order_type = str(t.get("order_type", "")).upper()
                    
                    if "sell" not in side and "orderside.sell" not in side:
                        continue
                        
                    sym = t.get("symbol")
                    if not sym: continue
                    
                    # 只有市价单(MO)或限价单(LO)导致的卖出才被视为“离场”或“止损离场”
                    is_execution_order = any(ot in order_type for ot in ["MO", "LO"])
                    
                    is_stop_loss = is_execution_order and (
                        any(k in reason for k in ["hard stop", "割肉", "扫损", "跌破", "破位", "亏损"]) or 
                        ("止损" in reason and not any(k in reason for k in ["盈利", "止盈", "锁定利润"]))
                    )
                    is_weed = is_execution_order and ("weed" in reason or "淘汰" in reason or "杂草" in reason)
                    
                    if is_stop_loss:
                        trade_unix = t.get("timestamp", {}).get("unix", 0)
                        hours_passed = (now_unix - trade_unix) / 3600
                        
                        # 4小时内日内冷静期
                        if hours_passed < 4.0:
                            if sym not in [r["symbol"] for r in recently_weeded]:
                                recently_weeded.append({
                                    "symbol": sym, 
                                    "type": "INTRADAY_STOP", 
                                    "hours_left": round(4.0 - hours_passed, 1),
                                    "days_left": 3
                                })
                        # 3天内长冷静期
                        elif days_ago <= 3:
                            if sym not in [r["symbol"] for r in recently_weeded]:
                                recently_weeded.append({
                                    "symbol": sym, 
                                    "type": "HARD_STOP", 
                                    "days_left": 3 - days_ago
                                })
                                
                    elif is_weed and days_ago <= 1:
                        if not any(r["symbol"] == sym for r in recently_weeded):
                            recently_weeded.append({"symbol": sym, "type": "SOFT_WEED", "days_left": 1 - days_ago})
                                
            if recently_weeded:
                logger.info(f"[Portfolio Manager] 最近淘汰/止损状态列表: {recently_weeded}")
        except Exception as e:
            logger.error(f"[Portfolio Manager] 提取最近淘汰/止损记录失败: {e}")
        directives["recently_weeded_out"] = recently_weeded

        if not current_positions:
            logger.info("[Portfolio Manager] 当前空仓，无需计算相对强度。")
            return directives
            
        # 4. 板块集中度惩罚
        directives["sector_limits"] = {
            "instruction": f"如果拟买入标的所属板块已经占总仓位的 {max_sector_limit}% 以上，触发相关性降级惩罚，拒绝买入。"
        }
        
        # 3. 优胜劣汰 (Weed & Flower) - 计算相对强度
        directives["weed_out_list"] = self._identify_weeds(current_positions)
        
        return directives
        
    def _calculate_adaptive_threshold(self, macro_risk: Dict) -> int:
        """
        根据最近交易历史的胜率，动态调整风控要求的买入门槛 (0-100)。
        """
        base_threshold = 50 if macro_risk.get("score", 50) >= 50 else 70
        
        try:
            logs = self.trade_logger.get_recent_logs(days=14)
            winning_trades = 0
            total_trades = 0
            
            # 统计平仓单胜率
            for log in logs:
                for t in log.get("trades", []):
                    # 如果记录了 profit_pct 或者卖出方向，可以统计
                    if t.get("action") == "SELL" and "profit" in str(t.get("reason", "")).lower():
                        winning_trades += 1
                        total_trades += 1
                    elif t.get("action") == "SELL" and "stop" in str(t.get("reason", "")).lower():
                        total_trades += 1
            
            if total_trades >= 5:
                win_rate = winning_trades / total_trades
                logger.info(f"[Portfolio Manager] 近期胜率: {win_rate*100:.1f}% ({winning_trades}/{total_trades})")
                
                # 胜率过低，开启现金防守，提高买入门槛
                if win_rate < 0.4:
                    base_threshold += 15
                    logger.warning(f"[Portfolio Manager] 胜率低下，防守模式开启！买入阈值提高至 {base_threshold}")
                # 胜率极高，可以适当下调门槛，减少现金闲置 (Cash Drag)
                elif win_rate > 0.65:
                    base_threshold = max(30, base_threshold - 10)
                    logger.info(f"[Portfolio Manager] 胜率极佳，进攻模式开启！买入阈值放宽至 {base_threshold}")
        except Exception as e:
            logger.error(f"[Portfolio Manager] 计算胜率自适应阈值失败: {e}")
            
        return base_threshold
        
    def _identify_weeds(self, positions: List[Dict]) -> List[str]:
        """
        对当前持仓进行相对强度 (RS) 排序，找出表现最差的 20% (哪怕没有触及止损)，
        标记为资金低效占用 (Weed)。
        为了避免误判新买入的仓位（新仓初始浮盈往往接近 0%），最近 5 天内有买入记录的股票将被排除，不列为杂草。
        """
        weeds = []
        try:
            # 1. 搜集最近5天买过的股票代码，作为保护名单（5天可完美跨越周末）
            recently_bought = set()
            try:
                recent_logs = self.trade_logger.get_recent_logs(days=5)
                for daily in recent_logs:
                    for t in daily.get("trades", []):
                        if t.get("side") in ["Buy", "OrderSide.Buy"]:
                            recently_bought.add(t.get("symbol"))
                if recently_bought:
                    logger.info(f"[Portfolio Manager] 最近5天买入保护名单 (不标记为杂草): {list(recently_bought)}")
            except Exception as e:
                logger.error(f"[Portfolio Manager] 获取最近买入记录失败: {e}")

            # 2. 解析持仓浮盈，排除保护名单中的股票
            parsed_positions = []
            for p in positions:
                sym = p.get("symbol")
                if sym in recently_bought:
                    logger.info(f"[Portfolio Manager] 持仓 {sym} 处于买入保护期内，跳过相对强度(杂草)判定")
                    continue
                pct_str = p.get("profit_pct", "0%")
                try:
                    pct_val = float(pct_str.replace("%", ""))
                except:
                    pct_val = 0.0
                parsed_positions.append({"symbol": sym, "profit": pct_val})
                
            # 按浮盈排序 (由低到高)
            sorted_pos = sorted(parsed_positions, key=lambda x: x["profit"])
            
            # 3. 如果剩余可评估持仓数 >= 4，挑出最差的进行淘汰
            if len(sorted_pos) >= 4:
                bottom_count = max(1, len(sorted_pos) // 4)  # 找出最后的 20%-25%
                for i in range(bottom_count):
                    candidate = sorted_pos[i]
                    # 只有当最差的票确实赚的少（甚至浮亏）时才标记为杂草，如果是全都暴赚就不淘汰
                    if candidate["profit"] < 3.0: 
                        weeds.append(candidate["symbol"])
                        
            if weeds:
                logger.warning(f"[Portfolio Manager] 🥀 优胜劣汰扫描: 发现资金低效占用持仓 (Weed): {weeds}，建议 CIO 寻找替换机会。")
                
        except Exception as e:
            logger.error(f"[Portfolio Manager] 优胜劣汰分析失败: {e}")
            
        return weeds
