#!/bin/bash

# LP-Agent 启动脚本 v2.0

APP_NAME="main.py"
LOG_FILE="agent.log"

echo "============================================================"
echo "  LP-Agent v2.0 启动程序"
echo "============================================================"

# 1. 检查并清理已有的旧进程
PID=$(ps -ef | grep "python3 -u $APP_NAME" | grep -v grep | awk '{print $2}')
if [ -n "$PID" ]; then
    echo "发现正在运行的旧进程 (PID: $PID)，正在关闭..."
    kill -9 $PID
    sleep 1
fi

# 2. 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "错误：未发现 .env 配置文件，请先创建并配置 Key。"
    exit 1
fi

# 3. 启动程序
echo "正在启动 $APP_NAME..."
nohup python3 -u $APP_NAME >> $LOG_FILE 2>&1 &

# 4. 确认启动状态
sleep 2
NEW_PID=$(ps -ef | grep "python3 -u $APP_NAME" | grep -v grep | awk '{print $2}')
if [ -n "$NEW_PID" ]; then
    echo "✅ 启动成功! (PID: $NEW_PID)"
    echo "日志正在输出到: $LOG_FILE"
    echo "你可以使用 'tail -f $LOG_FILE' 查看实时运行状态。"
else
    echo "❌ 启动失败，请检查 $LOG_FILE 查看错误原因。"
fi
echo "============================================================"
