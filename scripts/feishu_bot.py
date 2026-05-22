#!/usr/bin/env python3
"""feishu_bot.py — 飞书 Bot 消息发送与交互卡片"""

import os
import json
import time
import hmac
import hashlib
import base64
import logging
import requests

logger = logging.getLogger("feishu_bot")

# 加载 .env 文件（若 dotenv 可用）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_OPERATOR_OPEN_ID = os.environ.get("FEISHU_OPERATOR_OPEN_ID", "")
FEISHU_WEBHOOK_SECRET = os.environ.get("FEISHU_WEBHOOK_SECRET", "")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

_tenant_token_cache = {"token": "", "expires_at": 0}


def _enabled() -> bool:
    return bool(FEISHU_APP_ID and FEISHU_APP_SECRET)


def get_tenant_token() -> str:
    if not _enabled():
        return ""
    now = time.time()
    if _tenant_token_cache["token"] and now < _tenant_token_cache["expires_at"] - 300:
        return _tenant_token_cache["token"]
    try:
        resp = requests.post(
            f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        data = resp.json()
        token = data.get("tenant_access_token", "")
        expire = data.get("expire", 7200)
        _tenant_token_cache["token"] = token
        _tenant_token_cache["expires_at"] = now + expire
        return token
    except Exception as e:
        logger.warning(f"获取飞书 token 失败: {e}")
        return ""


def _post(path: str, body: dict) -> bool:
    if not _enabled():
        return False
    token = get_tenant_token()
    if not token:
        return False
    try:
        resp = requests.post(
            f"{FEISHU_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=5,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"飞书 API 失败: {e}")
        return False


def send_text(open_id: str, text: str) -> bool:
    return _post("/im/v1/messages?receive_id_type=open_id", {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    })


def send_card(open_id: str, card_json: dict) -> bool:
    return _post("/im/v1/messages?receive_id_type=open_id", {
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card_json, ensure_ascii=False),
    })


def send_alert(text: str) -> bool:
    if not FEISHU_OPERATOR_OPEN_ID:
        return False
    return send_text(FEISHU_OPERATOR_OPEN_ID, text)


def send_daily_summary(candidates: list[dict], date_str: str = "") -> bool:
    """发送每日候选摘要（纯通知 + Web UI 链接，不依赖飞书回调）"""
    if not FEISHU_OPERATOR_OPEN_ID:
        return False

    web_ui_url = "http://localhost:5000/"
    if not candidates:
        return send_text(FEISHU_OPERATOR_OPEN_ID,
                         f"📋 {date_str} 今日无新候选文章，请检查抓取任务")

    lines = [f"📋 {date_str} 今日候选 {len(candidates)} 篇，请前往 Web UI 审批：\n{web_ui_url}\n"]
    for i, c in enumerate(candidates[:5], 1):
        ts = c.get("title_score", 0)
        cs = c.get("content_score", 0)
        lines.append(f"{i}. [{ts:.1f}/{cs:.1f}] {c.get('title', '')}")
    if len(candidates) > 5:
        lines.append(f"... 还有 {len(candidates)-5} 篇")

    return send_text(FEISHU_OPERATOR_OPEN_ID, "\n".join(lines))


def build_daily_approval_card(candidates: list[dict]) -> dict:
    """构建每日审批交互卡片"""
    elements = []
    for c in candidates:
        article_text = f"{c.get('title', 'N/A')}\n评分：标题{c.get('title_score',0):.1f} 内容{c.get('content_score',0):.1f} | 话题：{c.get('topic','')}"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": article_text},
        })
        elements.append({
            "tag": "action",
            "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "发布"},
                 "type": "primary",
                 "value": json.dumps({"action": "approve", "news_key": c["key"], "pub_time": c.get("pub_time_recommended", "")})},
                {"tag": "button", "text": {"tag": "plain_text", "content": "跳过"},
                 "type": "default",
                 "value": json.dumps({"action": "skip", "news_key": c["key"]})},
                {"tag": "button", "text": {"tag": "plain_text", "content": "重生成"},
                 "type": "danger",
                 "value": json.dumps({"action": "regenerate", "news_key": c["key"]})},
            ],
        })
        elements.append({"tag": "hr"})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "今日发布审批"},
            "template": "blue",
        },
        "elements": elements,
    }


def build_weekly_report_card(report: dict, weight_suggestions: dict) -> dict:
    """构建周报卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "本周运营周报"},
            "template": "green",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": json.dumps(report, ensure_ascii=False)}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "一键采纳权重建议"},
                 "type": "primary",
                 "value": json.dumps({"action": "adopt_weights", "weights": weight_suggestions})},
            ]},
        ],
    }


def verify_feishu_signature(timestamp: str, nonce: str, body: bytes, sig: str) -> bool:
    if not FEISHU_WEBHOOK_SECRET:
        return True  # 未配置时不验证
    message = FEISHU_WEBHOOK_SECRET + timestamp + nonce
    if isinstance(body, bytes):
        message_b = message.encode() + body
    else:
        message_b = message.encode() + body.encode()
    expected = base64.b64encode(hmac.new(
        FEISHU_WEBHOOK_SECRET.encode(), message_b, hashlib.sha256
    ).digest()).decode()
    return hmac.compare_digest(expected, sig)
