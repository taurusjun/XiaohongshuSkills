"""同事件聚类（机械）：中文 3/4-gram shingles 重叠 + union-find。"""
import re

__all__ = ["tokens", "cluster", "group_of"]
_RUN = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]{2,}")


def tokens(text):
    out = set()
    for run in _RUN.findall(text or ""):
        if len(run) < 3:
            out.add(run)
            continue
        for n in (3, 4):
            for i in range(len(run) - n + 1):
                out.add(run[i:i + n])
    return out


def cluster(items, key_field="title", id_field="key", min_shared=3):
    """items:[{key,title,...}] → [[同事件条目,...], ...]（仅含 >=2 的组）。"""
    ids = [it[id_field] for it in items]
    toks = {it[id_field]: tokens(it.get(key_field, "")) for it in items}
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if len(toks[ids[i]] & toks[ids[j]]) >= min_shared:
                union(ids[i], ids[j])

    groups = {}
    for it in items:
        groups.setdefault(find(it[id_field]), []).append(it)
    return [g for g in groups.values() if len(g) > 1]


def group_of(item, items, key_field="title", id_field="key"):
    for g in cluster(items, key_field=key_field, id_field=id_field):
        if any(it[id_field] == item[id_field] for it in g):
            return g
    return [item]
