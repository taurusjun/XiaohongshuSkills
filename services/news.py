"""news 读写入口 —— 收敛所有对 news 表的访问到 services（唯一模块）。

MCP / CLI / 薄壳都应从这里取 DB 访问，而不是各自 import scripts.sqlite_db。
底层仍是 scripts.sqlite_db（WAL + busy_timeout=30000，见 P1 改动）。
"""
import sys

from services import paths

sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))

from sqlite_db import (  # noqa: E402,F401  re-export
    query_news, get_by_key, update_news,
    get_score_dims, load_active_dimensions,
    get_top_topics, get_config, set_config,
    rollback_dimension_version, commit_dimension_version,
    upsert_score_dims, load_dim_weights, recalculate_scores,
    set_state, get_state, stats, init_db, _connect,
)
