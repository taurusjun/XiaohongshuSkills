"""守卫：**逻辑层严禁 k12（12 位前缀）**，只允许出现在「人看的展示」。

规则出处：AGENTS.md「ID 一律用完整 40 位 key，严禁 12 位前缀（k12）」。

判定：扫 agent/ services/ scripts/ cli/ web/ ops/ 下的 .py，
      出现 `[:12]` 或 `k12` 且**该行不是展示/注释**→ 测试失败。
展示白名单：行内含 print( / _log( / log.append( / flush=True / 注释符 / 文档字符串。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIRS = ("agent", "services", "scripts", "cli", "web", "ops")
PAT = re.compile(r"\[:12\](?!\d)|\bk12\b")   # [:12] 后面不能跟数字（排除 [:120] 这类截断）
ALLOW_HINTS = ("print(", "_log(", "log.append(", "flush=True", "#", '"""', "'''",
               # 非「文章 key」的 12 位截断（uuid/md5/标题/日志字段），显式放行：
               "uuid4().hex[:12]", "md5(", "hexdigest()[:12]", "get('title'", "rr.get('title'",
               "f.write(")


def test_no_k12_in_logic():
    bad = []
    for d in DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in str(p) or ".venv" in str(p):
                continue
            lines = p.read_text(encoding="utf-8", errors="ignore").split("\n")
            for i, ln in enumerate(lines, 1):
                if not PAT.search(ln):
                    continue
                # 回溯到「语句首行」（缩进不大于本行的上一行），整条语句任一行含展示提示即放行
                ind = len(ln) - len(ln.lstrip())
                stmt = [ln]
                j = i - 2
                while j >= 0:
                    prev = lines[j]
                    if not prev.strip():
                        break
                    if (len(prev) - len(prev.lstrip())) < ind:
                        stmt.append(prev)
                        break
                    stmt.append(prev)
                    j -= 1
                if not any(any(h in x for h in ALLOW_HINTS) for x in stmt):
                    bad.append(f"{p.relative_to(ROOT)}:{i}: {ln.strip()[:90]}")
    assert not bad, ("逻辑层出现 k12/[:12]（仅展示层允许，见 AGENTS.md）：\n" + "\n".join(bad))
