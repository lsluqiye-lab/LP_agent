import re

with open("main.py", "r", encoding="utf-8") as f:
    content = f.read()

# Define the new main function and run_strategic_cycle function
new_main = """
async def run_strategic_cycle(tool_registry, config, logger, orchestrator, agent, feishu_notifier, morning_briefing_sent, trade_logger):
    # Phase 1: 收集
    collected_data = phase1_collect_data(tool_registry, logger)
    # Phase 2: 风控
    risk_result = phase2_risk_scoring(collected_data, config, logger)
    # Phase 2.5: 候选
    candidates = phase2_5_extract_candidates(collected_data, risk_result, logger)
    # Phase 3: 专家 (Map)
    briefings_json = await phase3_map_experts(candidates, orchestrator, risk_result, logger)
    
    # 推送选股和专家分析到飞书 (开盘简报)
    if feishu_notifier and candidates and not morning_briefing_sent:
        risk_msg = f"🌡️ 【宏观风控简报】\\n评分: {risk_result['score']} | 等级: {risk_result['regime']}\\n核心逻辑: {risk_result['constraints']['message']}\\n"
        
        cand_details = []
        for c in candidates:
            reason = "持仓巡检" if c.get("type") == "POSITION" else "发现交易信号"
            cand_details.append(f"• {c['symbol']} ({reason})")
        
        selection_msg = "🔍 【选股清单】\\n" + "\\n".join(cand_details)
        feishu_notifier.send_text(f"{risk_msg}\\n{selection_msg}")
        morning_briefing_sent = True
    
    # Phase 4: 决策 (Reduce)
    result = await phase4_strategic_decision(agent, collected_data, briefings_json, logger)
    logger.info(f"本轮决策结论:\\n{result}")
    return morning_briefing_sent

def watchdog_check(tool_registry, logger):
    # Placeholder for the high-frequency watchdog logic.
    # In the future, this will read a tactical config and trigger hard stops or wake up the strategic agent.
    # logger.debug("[Watchdog] 正在监控硬止损和量价异动...")
    pass

def main():
    # 信号处理
    def _graceful_shutdown(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # 加载配置
    import os
    config = AppConfig.from_env(os.getenv("LLM_PROVIDER", "gemini"))
    config.longport.to_env()
    logger = setup_logger("strategic_agent", config.log)
    
    logger.info("=" * 60)
    logger.info("LP-Agent v3.0 (Watchdog + Strategic Brain) 启动")
    logger.info("=" * 60)

    try:
        primary_llm = create_llm(config.llm)
        analyst_llm = create_llm(config.analyst_llm) if config.analyst_llm else primary_llm
        logger.info(f"LLM初始化成功: Primary({primary_llm.model}), Analyst({analyst_llm.model})")
    except Exception as e:
        logger.error(f"LLM初始化失败: {e}")
        sys.exit(1)

    # 初始化组件
    tool_registry = ToolRegistry()
    tool_registry.register_all(create_trading_tools())
    tool_registry.register_all(create_market_data_tools())
    tool_registry.register_all(create_search_tools())

    orchestrator = ExpertOrchestrator(llm=analyst_llm)
    trading_memory = get_trading_memory()
    feishu_notifier = FeishuNotifier(
        webhook_url=config.feishu.webhook_url,
        app_id=config.feishu.app_id,
        app_secret=config.feishu.app_secret
    ) if config.feishu.enabled else None

    agent = ReActAgent(
        llm=primary_llm,
        tool_registry=tool_registry,
        system_prompt=STRATEGIC_SYSTEM_PROMPT,
        max_iterations=10,
        feishu_notifier=feishu_notifier,
        trading_memory=trading_memory,
        logger=logger
    )

    review_agent = ReviewAgent(llm=primary_llm, config=config.review, feishu_notifier=feishu_notifier, trading_memory=trading_memory, logger_instance=logger)
    trade_logger = get_trade_logger()

    # ── 主循环 ──
    eastern = pytz.timezone('US/Eastern')
    last_date = None
    morning_briefing_sent = False
    
    # 记录当天是否执行过深度分析
    run_history = {"morning": False, "afternoon": False}

    while True:
        try:
            current_time = datetime.now(eastern)
            current_date = current_time.strftime('%Y-%m-%d')

            if current_date != last_date:
                review_agent.reset_daily_flag()
                last_date = current_date
                morning_briefing_sent = False
                run_history = {"morning": False, "afternoon": False}
                logger.info(f"新交易日: {current_date}")

            if not is_trading_hours(current_time):
                if review_agent.should_run():
                    phase5_daily_review(review_agent, logger)
                else:
                    logger.info("非交易时段，休眠中...")
                    time.sleep(60) # Sleep longer when market is closed
                continue
                
            # 交易时段: 运行高频 Watchdog
            watchdog_check(tool_registry, logger)

            # 交易时段: 判断是否需要唤醒深度决策层 (Strategic Brain)
            # 策略：每天 10:00 (开盘后消化完剧烈波动) 和 15:30 (收盘前确定日线形态)
            should_run_strategic = False
            time_str = current_time.strftime("%H:%M")
            
            if "10:00" <= time_str < "10:10" and not run_history["morning"]:
                logger.info("[Strategic Brain] 触发早盘深度决策时间 (10:00)")
                should_run_strategic = True
                run_history["morning"] = True
            elif "15:30" <= time_str < "15:40" and not run_history["afternoon"]:
                logger.info("[Strategic Brain] 触发尾盘深度决策时间 (15:30)")
                should_run_strategic = True
                run_history["afternoon"] = True
                
            if should_run_strategic:
                try:
                    loop = asyncio.get_event_loop()
                    morning_briefing_sent = loop.run_until_complete(
                        run_strategic_cycle(tool_registry, config, logger, orchestrator, agent, feishu_notifier, morning_briefing_sent, trade_logger)
                    )
                except Exception as e:
                    logger.error(f"深度决策层执行异常: {e}", exc_info=True)
                    trade_logger.log_error("cycle_error", str(e))

            # Watchdog 循环频率：1 分钟
            time.sleep(60)

        except KeyboardInterrupt:
            logger.info("收到中断信号，正在退出...")
            break
        except Exception as e:
            logger.error(f"主循环出错: {e}", exc_info=True)
            time.sleep(60)

    logger.info("LP-Agent v3.0 已退出")

if __name__ == "__main__":
    main()
"""

# Replace the original main function and end of file
new_content = re.sub(r'def main\(\):.*if __name__ == "__main__":\n    main\(\)\n*', new_main, content, flags=re.DOTALL)

with open("main.py", "w", encoding="utf-8") as f:
    f.write(new_content)
