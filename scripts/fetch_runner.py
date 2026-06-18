#!/usr/bin/env python3
"""fetch_runner.py — 每日定时抓取，从 DB 读 keywords 后调用 /api/trigger-fetch。"""

import sys
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [fetch_runner] %(message)s",
)
logger = logging.getLogger("fetch_runner")

WEBAPP_URL    = "http://127.0.0.1:5000"
POLL_INTERVAL = 60     # seconds between status polls
POLL_TIMEOUT  = 7200  # give up after 2 hours (90 articles × ~1min each)


def get_keywords() -> list[dict]:
    """从 webapp 读取预置词 + 自定义词，合并返回。"""
    preset = requests.get(f"{WEBAPP_URL}/api/keywords",        timeout=10).json().get("keywords", [])
    custom = requests.get(f"{WEBAPP_URL}/api/custom-keywords", timeout=10).json().get("keywords", [])
    merged  = [{"keyword": k["keyword"], "max": k["max"]} for k in preset]
    merged += [{"keyword": k["keyword"], "max": k["max"]} for k in custom]
    return merged


def trigger_fetch(keywords: list[dict]) -> str:
    """触发抓取，返回 task_id。locked 时抛出 RuntimeError。"""
    resp = requests.post(
        f"{WEBAPP_URL}/api/trigger-fetch",
        json={"mode": "keywords", "keywords": keywords},
        timeout=10,
    ).json()
    if resp.get("locked"):
        raise RuntimeError(f"抓取任务被锁: {resp.get('msg')}")
    return resp["task_id"]


def wait_for_task(task_id: str) -> tuple[bool, str]:
    """轮询任务状态直到完成。返回 (成功?, 最后日志片段)。"""
    deadline = time.time() + POLL_TIMEOUT
    last_log = ""
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            status = requests.get(f"{WEBAPP_URL}/api/task/{task_id}", timeout=10).json()
        except Exception as e:
            logger.warning(f"  轮询失败: {e}")
            continue
        s = status.get("status", "")
        last_log = status.get("log", "")
        logger.info(f"  task {task_id} status={s}")
        if s == "done":
            return True, last_log
        if s.startswith("error"):
            logger.error(f"  任务失败: {s}\n{last_log[-500:]}")
            return False, f"{s}\n{last_log[-300:]}"
    msg = f"任务超时（>{POLL_TIMEOUT}s）"
    logger.error(msg)
    return False, msg


def notify(ok: bool, keywords: list[dict], last_log: str, today_before: int) -> None:
    """完成后发飞书通知。"""
    try:
        from sqlite_db import stats
        s = stats()
        today_new = max(0, s["today"] - today_before)
        kw_str = "、".join(k["keyword"] for k in keywords)
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        if ok:
            text = (
                f"✅ 抓取完成 [{date_str}]\n"
                f"关键词：{kw_str}\n"
                f"今日新增：{today_new} 篇（今日合计 {s['today']}）\n"
                f"待发布：{s['pending']} 篇"
            )
        else:
            text = (
                f"❌ 抓取失败 [{date_str}]\n"
                f"关键词：{kw_str}\n"
                f"错误：{last_log[:200]}"
            )
        from feishu_bot import send_alert
        sent = send_alert(text)
        if sent:
            logger.info("飞书通知已发送")
        else:
            logger.warning("飞书通知发送失败（无凭证或接口异常）")
    except Exception as e:
        logger.warning(f"飞书通知异常: {e}")


def main():
    logger.info("=== fetch_runner 启动 ===")

    # 1. 读 keywords
    try:
        keywords = get_keywords()
    except Exception as e:
        logger.error(f"读取 keywords 失败: {e}")
        sys.exit(1)

    if not keywords:
        logger.error("keywords 为空，退出")
        sys.exit(1)

    logger.info(f"关键词: {json.dumps(keywords, ensure_ascii=False)}")

    # 记录抓取前的今日文章数，用于计算新增
    today_before = 0
    try:
        from sqlite_db import stats
        today_before = stats()["today"]
    except Exception:
        pass

    # 2. 触发抓取
    try:
        task_id = trigger_fetch(keywords)
    except RuntimeError as e:
        logger.warning(str(e))
        sys.exit(0)
    except Exception as e:
        logger.error(f"触发抓取失败: {e}")
        sys.exit(1)

    logger.info(f"任务已启动 task_id={task_id}")

    # 3. 等待完成
    ok, last_log = wait_for_task(task_id)

    # 4. 飞书通知
    notify(ok, keywords, last_log, today_before)

    if ok:
        logger.info("=== 抓取完成 ===")
    else:
        logger.warning("=== 抓取异常结束 ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
