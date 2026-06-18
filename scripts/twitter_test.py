#!/usr/bin/env python3
"""Twitter API 连通性测试 + 发一条测试推文。"""

import os
import requests
import tweepy

# 代理配置（Clash SOCKS5）
PROXY = "socks5h://127.0.0.1:10090"
os.environ["HTTP_PROXY"]  = PROXY
os.environ["HTTPS_PROXY"] = PROXY
os.environ["http_proxy"]  = PROXY
os.environ["https_proxy"] = PROXY

# 从环境变量读取（优先），fallback 到下方配置
API_KEY              = os.environ.get("TWITTER_API_KEY",              "jkFYuazT1kfwY12yUTQ3kmboc")
API_SECRET           = os.environ.get("TWITTER_API_SECRET",           "u6HgT3FmJbrcnmorsrgyzGME5RgtGecbs2aEshFkmtXy3Mnryd")
ACCESS_TOKEN         = os.environ.get("TWITTER_ACCESS_TOKEN",         "2065348629351759872-HCgs01v98jk87uwi3SvMIFz8LAgZIQ")
ACCESS_TOKEN_SECRET  = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET",  "Fx5rVilh7LTxnsByTN9mbLD3d3eGS5VDoxNen6AVNqwbZ")

def test_proxy():
    print("=== 1. 测试代理连通性 ===")
    try:
        r = requests.get(
            "https://api.twitter.com/2/tweets",
            proxies={"http": PROXY, "https": PROXY},
            timeout=10
        )
        print(f"  HTTP {r.status_code} — 代理可达")
    except Exception as e:
        print(f"  ❌ 代理失败: {e}")
        return False
    return True

def test_auth():
    print("\n=== 2. 测试 OAuth 1.0a 认证 ===")
    try:
        client = tweepy.Client(
            consumer_key=API_KEY,
            consumer_secret=API_SECRET,
            access_token=ACCESS_TOKEN,
            access_token_secret=ACCESS_TOKEN_SECRET,
        )
        # 验证凭证：get_me()
        me = client.get_me()
        print(f"  ✅ 认证成功，账号: @{me.data.username} (id={me.data.id})")
        return client
    except Exception as e:
        print(f"  ❌ 认证失败: {e}")
        return None

def post_test_tweet(client):
    print("\n=== 3. 发测试推文 ===")
    try:
        resp = client.create_tweet(
            text="🗾 Japan Entertainment Report — bot test tweet. [auto-delete soon]"
        )
        tweet_id = resp.data["id"]
        print(f"  ✅ 发推成功！tweet_id={tweet_id}")
        print(f"  URL: https://twitter.com/i/web/status/{tweet_id}")
        return tweet_id
    except Exception as e:
        print(f"  ❌ 发推失败: {e}")
        return None

if __name__ == "__main__":
    if not test_proxy():
        exit(1)
    client = test_auth()
    if not client:
        exit(1)
    post_test_tweet(client)
