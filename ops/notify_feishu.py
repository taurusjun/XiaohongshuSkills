#!/usr/bin/env python3
"""宿主侧飞书告警（仅标准库，无 requests）：读 scripts/.env 或环境变量，发文本给 FEISHU_OPERATOR_OPEN_ID。
用法：python3 notify_feishu.py "消息内容"   （成功返回 0）
"""
import json
import os
import sys
import urllib.request

BASE = "https://open.feishu.cn/open-apis"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    envf = os.path.join(REPO, "scripts", ".env")
    if os.path.exists(envf):
        for ln in open(envf, encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _post(url, body, headers=None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 直连，不走代理
    with opener.open(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    _load_env()
    text = sys.argv[1] if len(sys.argv) > 1 else "XHS 告警（无内容）"
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    open_id = os.environ.get("FEISHU_OPERATOR_OPEN_ID", "")
    if not (app_id and app_secret and open_id):
        print("缺少 FEISHU_APP_ID/APP_SECRET/OPERATOR_OPEN_ID")
        return 2
    try:
        tok = _post(f"{BASE}/auth/v3/tenant_access_token/internal",
                    {"app_id": app_id, "app_secret": app_secret}).get("tenant_access_token", "")
        if not tok:
            print("获取 token 失败"); return 3
        r = _post(f"{BASE}/im/v1/messages?receive_id_type=open_id",
                  {"receive_id": open_id, "msg_type": "text",
                   "content": json.dumps({"text": text}, ensure_ascii=False)},
                  headers={"Authorization": f"Bearer {tok}"})
        ok = r.get("code") == 0
        print("sent" if ok else f"fail: {r}")
        return 0 if ok else 4
    except Exception as e:                                   # noqa: BLE001
        print("发送异常: %r" % e)
        return 5


if __name__ == "__main__":
    sys.exit(main())
