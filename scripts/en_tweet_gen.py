#!/usr/bin/env python3
"""en_tweet_gen.py — 英文推文生成器
对已有 en_content 但缺 en_tweet 的文章，用 LLM 生成符合 Twitter 规范的推文。
API Key 从 scripts/.env 读取（LITELLM_API_KEY）。
"""
import json
import urllib.request
import sqlite3
import os
import sys
from pathlib import Path

DB  = "/Users/user/PG/XiaohongshuSkills/data/news_dev.db"
API = "http://127.0.0.1:5000"
ENV_FILE = Path(__file__).parent / ".env"

def load_env():
    env = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    except Exception as e:
        print(f"WARNING: could not load .env: {e}", file=sys.stderr)
    return env

ENV = load_env()
DEEPSEEK_URL   = ENV.get("LITELLM_URL",   "https://api.deepseek.com") + "/chat/completions"
DEEPSEEK_KEY   = ENV.get("LITELLM_API_KEY", os.environ.get("LITELLM_API_KEY", ""))
DEEPSEEK_MODEL = ENV.get("LITELLM_MODEL", "deepseek-chat")

def filter_tags_for_hashtag(tags_str: str) -> str:
    """只保留英文/罗马字 tag，过滤掉含 CJK 字符的 tag。"""
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    clean = []
    for t in tags:
        if not any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' for c in t):
            clean.append(t)
    return ", ".join(clean) if clean else tags_str


PROMPT = """You are an English tweet writer for a Japanese entertainment news account (@JapanEntRept).

Article title: {title}
Tags: {tags}
English article body (first 2000 chars):
{en_content}

Write ONE tweet (≤280 chars total) in English only.
Rules:
- Lead with the news hook or key scene
- Include 2-4 specific hashtags from the tags (English/romaji only, e.g. #FRUITSZIPPER #AKB48)
- NO generic tags like #JPop #idol #Japan
- NO Japanese or Chinese characters anywhere (including hashtags)
- Must be ≤280 chars — rewrite shorter if needed, never truncate mid-word

Output the tweet text only, no extra commentary."""

def get_candidates():
    conn = sqlite3.connect(DB)
    cur  = conn.cursor()
    cur.execute(
        "SELECT key FROM news WHERE en_content IS NOT NULL AND en_content != ''"
        " AND (en_tweet IS NULL OR en_tweet = '') ORDER BY created_at DESC"
    )
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows

def get_news(key):
    req  = urllib.request.Request(f"{API}/api/news/{key}")
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

def call_llm(prompt):
    if not DEEPSEEK_KEY:
        print("ERROR: LITELLM_API_KEY not set in scripts/.env", file=sys.stderr)
        sys.exit(1)
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.7,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {DEEPSEEK_KEY}"},
        method="POST",
    )
    resp  = urllib.request.urlopen(req, timeout=60)
    data  = json.loads(resp.read())
    tweet = data["choices"][0]["message"]["content"].strip()
    # 去掉 LLM 偶尔加的前缀或引号
    for prefix in ["Here is the tweet:", "Tweet:", "tweet:", "推文:"]:
        if tweet.startswith(prefix):
            tweet = tweet[len(prefix):].strip()
    return tweet.strip('"\'')

def validate(tweet):
    errors = []
    if not tweet:
        errors.append("empty")
        return errors
    if len(tweet) > 280:
        errors.append(f"too long: {len(tweet)} chars")
    if any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' for c in tweet):
        errors.append("contains CJK characters")
    for bad in ["#JPop", "#JPOP", "#idol", "#IDOL", "#Japan"]:
        if bad.lower() in tweet.lower():
            errors.append(f"generic tag: {bad}")
    tags = [w for w in tweet.split() if w.startswith("#")]
    if len(tags) < 2:
        errors.append(f"too few hashtags: {len(tags)}")
    for tag in tags:
        if any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' for c in tag):
            errors.append(f"CJK in hashtag: {tag}")
    return errors

def write_tweet(key, tweet):
    payload = json.dumps({"en_tweet": tweet}).encode()
    req = urllib.request.Request(
        f"{API}/api/news/{key}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req, timeout=10)

def main():
    keys = get_candidates()
    if not keys:
        print("No candidates found.")
        return
    print(f"Found {len(keys)} candidate(s). Model: {DEEPSEEK_MODEL}")
    print(f"API URL: {DEEPSEEK_URL}\n")

    ok_count = skip_count = 0
    for key in keys:
        data       = get_news(key)
        title      = data.get("title", "")
        tags_raw   = data.get("tags", "") if isinstance(data.get("tags"), str) else ",".join(data.get("tags") or [])
        tags       = filter_tags_for_hashtag(tags_raw)
        en_content = (data.get("en_content") or "")[:2000]

        prompt = PROMPT.format(title=title, tags=tags, en_content=en_content)
        tweet  = ""
        for attempt in range(3):
            tweet  = call_llm(prompt)
            if not tweet:
                print(f"  attempt {attempt+1}: empty response, retrying...", flush=True)
                continue
            errors = validate(tweet)
            if not errors:
                break
            print(f"  attempt {attempt+1} failed ({len(tweet)} chars): {errors}", flush=True)

        errors = validate(tweet)
        if errors:
            print(f"SKIP {key[:12]}: {', '.join(errors)}")
            print(f"  tweet: {tweet[:120]}")
            skip_count += 1
            continue

        write_tweet(key, tweet)
        print(f"OK   {key[:12]} ({len(tweet):3d} chars): {tweet[:80]}...")
        ok_count += 1

    print(f"\nDone: {ok_count} written, {skip_count} skipped.")

if __name__ == "__main__":
    main()
