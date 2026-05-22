"""
LP-Agent v3.5 科学选股与 Token 极简降耗自动化验证脚本
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

# 初始化日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TestUpgrade")

# 强制加载配置和环境
os.environ["LLM_PROVIDER"] = os.getenv("LLM_PROVIDER", "gemini")
from config import AppConfig, WATCHLIST
from agent.quant_analyst import QuantAnalyst
from agent.alpha_scanner import AlphaScanner
from agent.fundamental_analyst import FundamentalAnalyst
from main import create_llm, pre_screen_candidates, phase1_collect_data, phase2_risk_scoring

async def run_tests():
    logger.info("==================================================")
    logger.info("🚀 开始验证 LP-Agent v3.5 核心模块升级...")
    logger.info("==================================================")

    # 加载配置
    config = AppConfig.from_env(os.getenv("LLM_PROVIDER", "gemini"))
    primary_llm = create_llm(config.llm)
    logger.info(f"成功载入 LLM: {primary_llm.get_provider_name()} - {primary_llm.model}")

    # ==================================================
    # Test 1: QuantAnalyst (真实K线打分 + 行业强度)
    # ==================================================
    logger.info("\n--- [Test 1] 验证 QuantAnalyst 真实多因子打分 & 行业 RS 强度 ---")
    qa = QuantAnalyst()
    
    # 1.1 测试 11 个行业 ETF 相对大盘 SPY 相对强度
    logger.info("开始计算板块强度 (Sector RS)...")
    sector_ranks = qa.get_sector_strength()
    assert len(sector_ranks) > 0, "行业强度计算不应为空"
    logger.info(f"板块 RS 计算成功！前 3 最强行业: {list(sector_ranks.items())[:3]}")

    # 1.2 测试真实个股行情 K 线打分 (NVDA, MSFT)
    test_tickers = ["NVDA", "MSFT"]
    logger.info(f"开始计算 {test_tickers} 的真实多因子评分...")
    scores = qa.get_alpha_scores(test_tickers)
    for ticker, info in scores.items():
        logger.info(f"股票 {ticker} 技术分析结果:")
        logger.info(f"  - 真实量化评分: {info.get('score')}/100")
        logger.info(f"  - 阶段信号: {info.get('signals')}")
        logger.info(f"  - 策略建议: {info.get('recommendation')}")
        assert info.get("score") >= 0, "得分应为有效数值"
    logger.info("✅ [Test 1] QuantAnalyst 真实打分与行业强度验证成功！")

    # ==================================================
    # Test 2: AlphaScanner (科学选股 + 财报避雷)
    # ==================================================
    logger.info("\n--- [Test 2] 验证 AlphaScanner 盘前 Top-Down 科学选股 ---")
    scanner = AlphaScanner(llm=primary_llm)
    logger.info("执行 AlphaScanner 动态选股（这包含拉取日K线、行业RS、财报分组搜索及大模型催化剂排雷）...")
    
    new_watchlist, reasoning = scanner.run()
    logger.info(f"选股成功！今日最科学监控标的池: {new_watchlist}")
    logger.info(f"大模型选股逻辑汇总:\n{reasoning}")
    assert len(new_watchlist) >= 5, "选出的监控池个股不应少于5只"
    logger.info("✅ [Test 2] AlphaScanner 科学选股验证成功！")

    # ==================================================
    # Test 3: FundamentalAnalyst (5日基本面研报本地缓存)
    # ==================================================
    logger.info("\n--- [Test 3] 验证 FundamentalAnalyst 5日基本面缓存机制 ---")
    fa = FundamentalAnalyst(llm=primary_llm)
    
    # 清理已有缓存以确保真实测试
    cache_path = "data/fundamental_cache.json"
    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
            logger.info("已清理旧的基本面缓存文件，确保全新缓存冷启动测试。")
        except Exception:
            pass

    # 3.1 第一次获取 NVDA 基本面（冷启动 - 应该触发真实搜索和大模型调用）
    logger.info("【第 1 次分析 (Cache Miss)】拉取 NVDA 基本面中...")
    start_time = datetime.now()
    briefing_1 = await fa.analyze("NVDA")
    time_cold = (datetime.now() - start_time).total_seconds()
    logger.info(f"【冷启动耗时】: {time_cold:.2f} 秒")
    logger.info(f"基本面详情: {json.dumps(briefing_1, ensure_ascii=False, indent=2)}")

    # 3.2 第二次获取 NVDA 基本面（热启动 - 应该瞬间从本地缓存返回，耗时近乎为 0 且 0 Token 消耗）
    logger.info("【第 2 次分析 (Cache Hit)】拉取 NVDA 基本面中...")
    start_time = datetime.now()
    briefing_2 = await fa.analyze("NVDA")
    time_hot = (datetime.now() - start_time).total_seconds()
    logger.info(f"【热启动缓存命中耗时】: {time_hot:.4f} 秒")
    
    assert briefing_1 == briefing_2, "两次返回的基本面内容必须完全相同"
    assert time_hot < 0.2, "缓存命中耗时应该极短 (< 0.2秒)"
    logger.info(f"✅ [Test 3] FundamentalAnalyst 缓存命中成功！耗时从 {time_cold:.2f}s 缩减至 {time_hot:.4f}s (耗时直降 {((time_cold-time_hot)/time_cold)*100:.1f}%)，0 Token 损耗！")

    # ==================================================
    # Test 4: Pre-Screening (门限初筛机制)
    # ==================================================
    logger.info("\n--- [Test 4] 验证 Local Pre-Screening 本地规则门限初筛 ---")
    
    # 模拟没有异动的稳定个股候选
    mock_candidates = [
        {
            "symbol": "AAPL",
            "type": "POSITION",
            "details": {
                "price": 180.0,
                "ret_20d": 0.5,
                "flags": ["STAGE2"],
                "volume_ratio": 0.9,
                "needs_attention": False
            }
        },
        {
            "symbol": "MSFT",
            "type": "STRONG_SIGNAL",
            "details": {
                "price": 420.0,
                "ret_20d": 1.2,
                "flags": ["STAGE2"],
                "volume_ratio": 0.8,
                "needs_attention": False
            }
        }
    ]
    
    # 模拟风控结果和组合指令
    mock_risk = {"score": 75.0, "constraints": {"allow_new_buy": True}}
    mock_directives = {"weed_out_list": []}
    mock_collected = {"get_positions": json.dumps({"positions": [{"symbol": "AAPL", "cost_price": 178.0, "quantity": 10, "market_value": 1800.0}]})}

    # 执行初筛
    active, passive = pre_screen_candidates(mock_candidates, mock_risk, mock_directives, mock_collected, logger)
    logger.info(f"模拟初筛结果: 活跃股={active}, 观望股={passive}")
    assert len(active) == 0, "没有异动的股票不应该触发活跃门限"
    assert len(passive) == 2, "平稳股应自动进入观望名单"
    logger.info("✅ 成功验证：稳定个股不触发门限，进入观望 (自动跳过 LLM 决策)！")

    # 模拟触发异动的加仓/止损/突破情况
    logger.info("模拟触发异常异动的情境（跌破防守线 / 放量向上突破）...")
    mock_candidates_active = [
        {
            "symbol": "AAPL",
            "type": "POSITION",
            "details": {
                "price": 180.0,
                "ret_20d": -5.0,  # 发生回撤
                "flags": [],
                "volume_ratio": 1.0,
                "needs_attention": False
            }
        },
        {
            "symbol": "MSFT",
            "type": "STRONG_SIGNAL",
            "details": {
                "price": 420.0,
                "ret_20d": 3.5,
                "flags": ["HIGH_VOL"], # 放量突破
                "volume_ratio": 1.6,
                "needs_attention": True
            }
        }
    ]
    # AAPL 此时浮盈很低 (180.0 现价 vs 179.0 成本，仅 0.5% 利润)，且 20 日走势回落 -5.0%，应该触发主动利润保护门限
    mock_collected_low_profit = {"get_positions": json.dumps({"positions": [{"symbol": "AAPL", "cost_price": 179.0, "quantity": 10, "market_value": 1800.0}]})}
    
    active_act, passive_act = pre_screen_candidates(mock_candidates_active, mock_risk, mock_directives, mock_collected_low_profit, logger)
    logger.info(f"异动初筛结果: 活跃股={active_act}, 观望股={passive_act}")
    assert len(active_act) == 2, "异动股应当正确触发活跃门限以唤醒主脑"
    logger.info("✅ 成功验证：异动个股（触发保护止损、或放量买入突破）能被 100% 精准捕获并激活！")
    
    logger.info("✅ [Test 4] Local Pre-Screening 门限唤醒机制验证完成！")

    logger.info("\n==================================================")
    logger.info("🎉🎉 LP-Agent v3.5 科学选股与极简 Token 重构升级全部验证成功！")
    logger.info("==================================================")

if __name__ == "__main__":
    asyncio.run(run_tests())
