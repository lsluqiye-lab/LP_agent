FROM ubuntu:20.04

# 设置环境变量避免交互式提示
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8

# 合并所有RUN命令以减少层数，并清理缓存
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    locales \
    tzdata \
    && locale-gen zh_CN.UTF-8 && \
    update-locale LANG=zh_CN.UTF-8 && \
    python3 -m pip install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir \
    longport \
    dashscope \
    holidays \
    pytz && \
    apt-get purge -y --auto-remove && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache

# 创建非root用户
RUN useradd -ms /bin/bash develop && \
    echo "develop:develop" | chpasswd && \
    adduser develop sudo && \
    mkdir -p /home/develop/workspace && \
    chown -R develop:develop /home/develop

# 切换用户和工作目录
USER develop
WORKDIR /home/develop/workspace

# 复制应用文件
COPY --chown=develop:develop LP-Agent.py .

# 设置默认命令
CMD ["python3", "LP-Agent.py"]
