"""E2E：import_drafts 走真实 webapp API 入库（无 API 时跳过）。

需要在容器内（webapp:5000 + 数据卷 DB）运行。
"""
import json
import sqlite3
import urllib.request

import pytest

from services import import_drafts as imp, paths


def _api_ok() -> bool:
    try:
        urllib.request.urlopen(paths.api_base() + "/api/news?limit=1", timeout=3)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _api_ok(), reason="webapp API 不可达（需容器内运行）")


def _get(key):
    with urllib.request.urlopen(paths.api_base() + f"/api/news/{key}", timeout=5) as r:
        return json.loads(r.read())


def test_import_drafts_end_to_end(tmp_path):
    db = paths.sqlite_path()
    ka = "1111e2e0" + "0" * 32
    kb = "2222e2e0" + "0" * 32
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO news (key,title,link,rewritten_title,rewritten_content,"
                 "publish_mode,preselected,publish_xhs,wechat_title,wechat_content,channel) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (ka, "A", "https://x", "", "", "", 0, 0, "", "", ""))
    conn.execute("INSERT OR REPLACE INTO news (key,title,link,rewritten_title,rewritten_content,"
                 "publish_mode,preselected,publish_xhs,wechat_title,wechat_content,channel) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (kb, "B", "https://x", "", "", "", 0, 0, "", "", ""))
    conn.commit(); conn.close()

    d = tmp_path / "drafts"; d.mkdir()
    (d / f"xhs_draft_{ka[:12]}.md").write_text("## E2E标题甲\n" + "正" * 120, encoding="utf-8")
    (d / f"gzh_draft_{kb[:12]}.md").write_text("## E2E标题乙\n" + "公" * 120, encoding="utf-8")

    ok, gzh_ok, fail = imp.import_drafts(str(d), None, {}, {kb[:12]})
    assert not fail, fail
    assert ok and gzh_ok
    a = _get(ka)
    assert a["rewritten_title"] == "E2E标题甲" and a["preselected"] == 1 and a["publish_xhs"] == 0
    b = _get(kb)
    assert b["channel"] == "gzh" and b["wechat_title"] == "E2E标题乙"

    c = sqlite3.connect(db); c.execute("DELETE FROM news WHERE key IN (?,?)", (ka, kb)); c.commit(); c.close()
