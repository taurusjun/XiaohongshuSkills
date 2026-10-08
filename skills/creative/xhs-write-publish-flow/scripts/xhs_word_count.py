#!/usr/bin/env python3
"""薄壳：唯一实现已迁至 services/word_count.py。保留 CLI 行为与 xhs_* 函数名。"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services.word_count import main, content_len as xhs_content_len, title_len as xhs_title_len  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
