#!/usr/bin/env bash

docker rm -f lp-agent >/dev/null 2>&1 || true

docker run -d \
  --env-file .env \
  --name lp-agent \
  --network host \
  -e PROXY_TYPE=http \
  -e PROXY_HOST=127.0.0.1 \
  -e PROXY_PORT=7897 \
  lp-agent:latest

echo "lp-agent 已在后台启动。查看日志: docker logs -f lp-agent"
