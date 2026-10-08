#!/bin/bash
# 薄壳：唯一实现在 services/draft_io.main_verify
DIR="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$DIR/../../../.." && pwd)"
exec "$ROOT/.venv/bin/python" -c "import sys; sys.path.insert(0,'$ROOT'); from services.draft_io import main_verify; sys.exit(main_verify())" "$@"
