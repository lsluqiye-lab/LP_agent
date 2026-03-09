"""
Gemini 搜索工具代理连通性测试
在你自己的 Mac 上运行此脚本，验证代理是否能访问 Google Gemini API

用法:
    export https_proxy='http://127.0.0.1:13659'
    export GEMINI_API_KEY='AIzaSyCCZ7QrwED0m1Qmp54b16zR8ToOD2EB0Q8'
    python3 test_gemini_proxy.py
"""
import os
import sys

def main():
    proxy = os.environ.get("https_proxy", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")

    print("=" * 50)
    print("  Gemini 代理连通性测试")
    print("=" * 50)
    print(f"  代理: {proxy or '未设置'}")
    print(f"  API Key: {api_key[:10]}..." if api_key else "  API Key: 未设置")
    print()

    # ── 测试1: httpx 能否通过代理连到 Google ──
    print("【测试1】httpx 通过代理访问 Google...")
    import httpx
    try:
        with httpx.Client(timeout=10) as c:  # 自动读 https_proxy
            r = c.get("https://generativelanguage.googleapis.com/")
            print(f"  ✅ 连通! HTTP {r.status_code}")
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")
        print()
        print("  ⚠️ 代理无法访问 Google API。阿里郎企业代理可能未放行此域名。")
        print("  💡 建议: 使用 Clash/V2Ray 等科学上网工具，端口通常是 7890 或 7897")
        print("     然后修改 start.sh 中的代理为对应端口。")
        return 1

    # ── 测试2: Gemini API 调用 ──
    print()
    print("【测试2】Gemini API 普通调用...")
    if not api_key:
        print("  ⏭️ GEMINI_API_KEY 未设置，跳过")
    else:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents="Say hello in one word",
                config=types.GenerateContentConfig(max_output_tokens=32),
            )
            text = resp.candidates[0].content.parts[0].text
            print(f"  ✅ 成功! 回复: {text.strip()}")
        except Exception as e:
            print(f"  ❌ 失败: {e}")

    # ── 测试3: Gemini + Google Search ──
    print()
    print("【测试3】Gemini + Google Search（搜索工具核心功能）...")
    if not api_key:
        print("  ⏭️ GEMINI_API_KEY 未设置，跳过")
    else:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents="What is the S&P 500 index level today?",
                config=types.GenerateContentConfig(
                    max_output_tokens=256,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            text = ""
            for part in resp.candidates[0].content.parts:
                if part.text:
                    text += part.text
            preview = text[:120].replace("\n", " ") + "..." if len(text) > 120 else text
            print(f"  ✅ 成功! 搜索结果: {preview}")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            if "location" in str(e).lower():
                print("  ⚠️ 仍然报 location 错误，说明代理出口 IP 仍在不支持的地区")
                print("  💡 需要使用海外节点的代理（美国/日本等）")

    print()
    print("=" * 50)
    return 0

if __name__ == "__main__":
    sys.exit(main())
