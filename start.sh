#!/bin/bash

# LP-Agent 启动脚本 v4.6.2

APP_NAME="main.py"
LOG_FILE="agent.log"
PYTHON_BIN="/root/gemini/gemini_venv/bin/python"

# 优雅降级：如果虚拟环境不存在，使用系统默认 python
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="python"
fi

echo "============================================================"
echo "  LP-Agent v4.6.2 启动程序 (Main-Line Aware Edition)"
echo "  使用 Python: $PYTHON_BIN"
echo "============================================================"

# 1. 检查并清理已有的旧进程 (基于进程物理工作路径进行隔离，防止干扰相邻环境的 main.py)
FOUND_PIDS=""
ALL_PIDS=$(pgrep -f "$APP_NAME")
for p in $ALL_PIDS; do
    if [ -d "/proc/$p" ]; then
        PROC_CWD=$(readlink -f "/proc/$p/cwd")
        CURR_CWD=$(pwd)
        if [ "$PROC_CWD" = "$CURR_CWD" ] && [ "$p" != "$$" ]; then
            FOUND_PIDS="$FOUND_PIDS $p"
        fi
    fi
done

if [ -n "$FOUND_PIDS" ]; then
    echo "发现正在此目录运行的旧进程 (PIDs:$FOUND_PIDS)，正在关闭..."
    for kill_pid in $FOUND_PIDS; do
        kill -9 $kill_pid 2>/dev/null || true
    done
    sleep 1
fi

# 2. 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "错误：未发现 .env 配置文件，请先创建并配置 Key。"
    exit 1
fi

# 3. 启动程序
echo "正在启动 $APP_NAME..."
nohup $PYTHON_BIN -u $APP_NAME >> $LOG_FILE 2>&1 &

# 4. 确认启动状态
sleep 2
NEW_PID=""
ALL_NEW_PIDS=$(pgrep -f "$APP_NAME")
for p in $ALL_NEW_PIDS; do
    if [ -d "/proc/$p" ]; then
        PROC_CWD=$(readlink -f "/proc/$p/cwd")
        CURR_CWD=$(pwd)
        if [ "$PROC_CWD" = "$CURR_CWD" ] && [ "$p" != "$$" ]; then
            NEW_PID=$p
            break
        fi
    fi
done

if [ -n "$NEW_PID" ]; then
    echo "✅ 启动成功! (PID: $NEW_PID)"
    echo "日志正在输出到: $LOG_FILE"
    echo "你可以使用 'tail -f $LOG_FILE' 查看实时运行状态。"
else
    echo "❌ 启动失败，请检查 $LOG_FILE 查看错误原因。"
fi
echo "============================================================"
