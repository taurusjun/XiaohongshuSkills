#!/usr/bin/env python3
"""薄壳：唯一实现在 services/draft_io.py（main_dump）。"""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from services.draft_io import main_dump  # noqa: E402
if __name__ == "__main__":
    sys.exit(main_dump())
