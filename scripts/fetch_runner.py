#!/usr/bin/env python3
"""fetch_runner.py — 每日定时抓取，从 DB 读 keywords 后调用 /api/trigger-fetch。"""

import sys
import json
import time
import logging
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [fetch_runner] %(message)s",
)
logger = logging.getLogger("fetch_runner")

WEBAPP_URL = "http://127.0.0.1:5000"
POLL_INTERVAL = 10   # seconds between status polls
POLL_TIMEOUT  = 1800 # give up after 30 minutes


def get_keywords() -> list[dict]:
    """从 webapp 读取预置词 + 自定义词，合并返回。"""
    preset  = requests.get(f"{WEBAPP_URL}/api/keywords",        timeout=10).json().get("keywords", [])
    custom  = requests.get(f"{WEBAPP_URL}/api/custom-keywords", timeout=10).json().get("keywords", [])
    # preset 含 topic 字段，只保留 keyword/max
    merged = [{"keyword": k["keyword"], "max": k["max"]} for k in preset]
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


def wait_for_task(task_id: str) -> bool:
    """轮询任务状态直到完成，返回是否成功。"""
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            status = requests.get(f"{WEBAPP_URL}/api/task/{task_id}", timeout=10).json()
        except Exception as e:
            logger.warning(f"  轮询失败: {e}")
            continue
        s = status.get("status", "")
        logger.info(f"  task {task_id} status={s}")
        if s == "done":
            return True
        if s.startswith("error"):
            logger.error(f"  任务失败: {s}\n{status.get('log','')[-500:]}")
            return False
    logger.error(f"  任务超时（>{POLL_TIMEOUT}s）")
    return False


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
    ok = wait_for_task(task_id)
    if ok:
        logger.info("=== 抓取完成 ===")
    else:
        logger.warning("=== 抓取异常结束 ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
