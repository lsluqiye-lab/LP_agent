#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Gemini 搜索工具 完整链路测试
# 请在你的 Mac 终端中直接运行此脚本
# 用法: bash test_gemini_local.sh
# ═══════════════════════════════════════════════════════════

set -e

if [ -z "$GEMINI_API_KEY" ]; then
  echo "错误：GEMINI_API_KEY 环境变量未设置。" >&2
  echo "请在运行此脚本前设置您的API密钥，例如：" >&2
  echo "export GEMINI_API_KEY='YOUR_REAL_API_KEY'" >&2
  exit 1
fi

echo "============================================================"
echo "  Gemini 搜索工具本地测试"
echo "  请在你的 Mac 上运行此脚本（不要在 VM 中运行）"
echo "============================================================"
echo ""

# ── Step 1: 检测代理 ──
echo "【Step 1】检测代理环境..."
echo "  http_proxy=${http_proxy:-未设置}"
echo "  https_proxy=${https_proxy:-未设置}"
echo ""

# ── Step 2: 检查出口 IP ──
echo "【Step 2】检查网络出口 IP..."
IP_INFO=$(curl -s https://ipinfo.io/json 2>/dev/null || echo '{"error":"failed"}')
echo "  $IP_INFO" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  IP: {d.get(\"ip\",\"?\")}, 地区: {d.get(\"city\",\"?\")} {d.get(\"country\",\"?\")}')
    if d.get('country') == 'CN':
        print('  ⚠️ 出口IP在中国大陆，Google API 可能被阻止')
    else:
        print('  ✅ 出口IP不在中国大陆')
except:
    print('  无法解析 IP 信息')
"
echo ""

# ── Step 3-6: Python 测试 ──
python3 << 'PYEOF'
import os, json

for k in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
    os.environ.pop(k, None)

from google import genai
from google.genai import types

api_key = os.environ["GEMINI_API_KEY"]
client = genai.Client(api_key=api_key)
model = "gemini-3.1-pro-preview"

# ── 测试3: 普通对话 ──
print("【Step 3】Gemini 普通对话（无搜索，无代理）...")
try:
    resp = client.models.generate_content(
        model=model,
        contents="Say hello in one sentence.",
        config=types.GenerateContentConfig(max_output_tokens=64),
    )
    text = resp.candidates[0].content.parts[0].text.strip()
    print(f"  ✅ 成功: {text}")
except Exception as e:
    err = str(e)
    if "location" in err.lower():
        print(f"  ❌ 地区限制! Google 不允许从当前 IP 访问 Gemini API")
        print(f"  💡 需要配置可访问海外的代理（如 Clash/V2Ray），阿里郎代理可能不行")
    else:
        print(f"  ❌ 失败: {err[:200]}")
print()

# ── 测试4: 带 Google Search 的美股搜索 ──
print("【Step 4】Gemini + Google Search（美股宏观经济）...")
try:
    resp = client.models.generate_content(
        model=model,
        contents="请用中文总结今天美股三大指数（道琼斯、标普500、纳斯达克）的最新行情和重要财经新闻",
        config=types.GenerateContentConfig(
            max_output_tokens=2048,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    print("  --- 搜索回复 ---")
    for part in resp.candidates[0].content.parts:
        if part.text:
            print(f"  {part.text}")
    
    meta = resp.candidates[0].grounding_metadata
    if meta and meta.grounding_chunks:
        print()
        print("  --- 引用来源 ---")
        for i, chunk in enumerate(meta.grounding_chunks[:5]):
            if chunk.web:
                print(f"  [{i+1}] {chunk.web.title}")
                print(f"      {chunk.web.uri}")
    print()
    print("  ✅ 宏观经济搜索成功!")
except Exception as e:
    print(f"  ❌ 失败: {str(e)[:200]}")
print()

# ── 测试5: 个股新闻搜索 ──
print("【Step 5】Gemini + Google Search（NVDA 个股新闻）...")
try:
    resp = client.models.generate_content(
        model=model,
        contents="请用中文总结 NVIDIA (NVDA) 最近一周的重要新闻、分析师评级变动和股价走势",
        config=types.GenerateContentConfig(
            max_output_tokens=2048,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    print("  --- 搜索回复 ---")
    for part in resp.candidates[0].content.parts:
        if part.text:
            print(f"  {part.text}")
    
    meta = resp.candidates[0].grounding_metadata
    if meta and meta.grounding_chunks:
        print()
        print("  --- 引用来源 ---")
        for i, chunk in enumerate(meta.grounding_chunks[:5]):
            if chunk.web:
                print(f"  [{i+1}] {chunk.web.title}")
                print(f"      {chunk.web.uri}")
    print()
    print("  ✅ 个股新闻搜索成功!")
except Exception as e:
    print(f"  ❌ 失败: {str(e)[:200]}")
print()

# ── 测试6: 社交舆情搜索 ──
print("【Step 6】Gemini + Google Search（市场舆情）...")
try:
    resp = client.models.generate_content(
        model=model,
        contents="请用中文总结当前 Reddit WallStreetBets 和 Twitter 上关于美股 SPY 和大盘走势的散户情绪和讨论热点",
        config=types.GenerateContentConfig(
            max_output_tokens=2048,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    print("  --- 搜索回复 ---")
    for part in resp.candidates[0].content.parts:
        if part.text:
            print(f"  {part.text}")
    
    meta = resp.candidates[0].grounding_metadata
    if meta and meta.grounding_chunks:
        print()
        print("  --- 引用来源 ---")
        for i, chunk in enumerate(meta.grounding_chunks[:5]):
            if chunk.web:
                print(f"  [{i+1}] {chunk.web.title}")
                print(f"      {chunk.web.uri}")
    print()
    print("  ✅ 舆情搜索成功!")
except Exception as e:
    print(f"  ❌ 失败: {str(e)[:200]}")

print()
print("=" * 60)
print("  测试完成!")
print("=" * 60)
PYEOF
