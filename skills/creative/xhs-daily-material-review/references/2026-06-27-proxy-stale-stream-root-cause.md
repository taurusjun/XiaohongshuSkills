# 2026-06-27: DeepSeek Stale Stream Root Cause — HTTP_PROXY Env Var

## Symptoms

- cron job `775eb50bf445` (每日素材review) fires at 10:00 CST
- Runs API calls #1~#16 fine (tool calls: skill_view, todo, terminal, etc.)
- API call #1 took 780s (first 4 attempts killed by stale stream, 5th got through)
- When the agent needs to make the 17th API call (analyze ~49K context and produce review), DeepSeek API stalls
- Repeated `Stream stale for 180s — no chunks received. Killing connection.`
- After 3 stale kills (9 minutes), the job silently exits without delivering to Telegram
- `last_status` shows `ok` in `cronjob list` but the user received nothing

## Timeline (from agent.log)

```
10:00:03  — Task triggered
10:03:04  — WARNING Stream stale 180s (18K ctx) ← stale kill #1
10:06:04  — WARNING Stream stale 180s (18K ctx) ← stale kill #2  
10:09:05  — WARNING Stream stale 180s (18K ctx) ← stale kill #3
10:12:05  — WARNING Stream stale 180s (18K ctx) ← stale kill #4
10:13:03  — API call #1 finally succeeds (latency 780s = 4 kills × 180s + 60s)
10:13~15 — API calls #2~#16 succeed (tool execution phase, medium context ~20-64K)
10:15:05  — Last successful tool call (terminal returns 16,602 chars of news data)
10:18:06  — WARNING Stream stale 180s (49K ctx) ← stale kill #17
10:21:06  — WARNING Stream stale 180s (49K ctx) ← stale kill #18
10:24:06  — WARNING Stream stale 180s (49K ctx) ← stale kill #19
          — Job silently dies, no final delivery
```

Note: `stale_stream_kill` appears in agent.log for ALL API calls that timed out, followed by `OpenAI client aborted (stale_stream_kill)`.

## Root Cause

**Global `HTTP_PROXY` / `HTTPS_PROXY` environment variables set to `socks5h://127.0.0.1:10090`.**

Check:
```bash
echo "HTTP_PROXY=$HTTP_PROXY"
echo "HTTPS_PROXY=$HTTPS_PROXY"
```

All subprocesses spawned by Hermes (including cron job agent sessions) inherit these env vars. DeepSeek API (`api.deepseek.com`) is a **Chinese service that does not need a proxy** — it should be connected directly.

Proxy comparison:
- **Direct (no proxy):** `curl https://api.deepseek.com/v1/models` → 0.075s (75ms)
- **Via SOCKS5:** → >180s timeout, stale stream kills

The SOCKS5 proxy at `127.0.0.1:10090` is configured for:
- Telegram gateway (in `config.yaml`: `gateway.telegram.proxy`)
- X/Twitter API calls
- Any service blocked by the Great Firewall

It should **NOT** be used for:
- DeepSeek API (`api.deepseek.com`)
- Any Chinese-service API

## Fix Options

### Option 1: no_proxy env var (fastest)

Add `api.deepseek.com` and `deepseek.com` to `no_proxy`:
```bash
export no_proxy=".deepseek.com,localhost,127.0.0.1"
```
This needs to be in the environment that Hermes starts in (e.g. `~/.hermes/.env`, or the shell profile that launches Hermes).

### Option 2: Per-session unset in cron script

The cron scripts already run shell commands. If a cron job specifically uses DeepSeek, unset the proxy before the agent session:
```bash
# Not possible — the cron agent runs as a subprocess that inherits env
```

### Option 3: Unset in shell profile / launch script

Remove `HTTP_PROXY` / `HTTPS_PROXY` from the global shell profile and instead set them only in the Telegram gateway config:
```yaml
# config.yaml
gateway:
  telegram:
    proxy: socks5://127.0.0.1:10090
```

This way only Telegram uses the proxy, not all Hermes API calls.

### Option 4: No proxy in env at all — only in gateway config

Ideal long-term fix: remove `export HTTP_PROXY=...` from all shell profiles and `.env`. The Telegram gateway proxy config in `config.yaml` already handles Telegram traffic. Everything else (DeepSeek, OpenRouter, Anhropic, etc.) should connect directly.

## Diagnostic Commands

```bash
# Check current proxy env vars
env | grep -i proxy

# Check which provider Hermes is using
grep "provider:" ~/.hermes/config.yaml | head -3

# Test DeepSeek direct vs proxy
echo "=== Direct ===" && curl -s -o /dev/null -w "HTTP %{http_code} time=%{time_total}s\n" https://api.deepseek.com/v1/models -H "Authorization: Bearer test" 2>&1
echo "=== Via SOCKS5 ===" && curl -x socks5h://127.0.0.1:10090 -s -o /dev/null -w "HTTP %{http_code} time=%{time_total}s\n" https://api.deepseek.com/v1/models -H "Authorization: Bearer test" 2>&1

# Check if proxy is in config.yaml
grep -n "proxy" ~/.hermes/config.yaml

# Check agent.log for recent stale stream kills in ANY job
grep "stale_stream_kill\|Stream stale" ~/.hermes/logs/agent.log | tail -20
```
