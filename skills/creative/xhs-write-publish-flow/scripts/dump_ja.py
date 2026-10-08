#!/usr/bin/env python3
"""按 key 前缀 dump content_ja（**强制折行**）—— 阶段A读素材的标准工具。

为什么必须有这个脚本
--------------------
`read_file` 会把超长行**静默截断**（实测单行 ~2000 字上限）。直接把 content_ja
写进 /tmp/xxx.txt 再 read_file，5000 字的 live report 只能读到前 2400 字，中后段
的引语/成员发言全部丢失，而输出里只会出现一个 `... [truncated]` 标记，不报错。
10/1 批实测：95ff1f64（ja 5347）靠 read_file 只拿到约 2400 字，改成本脚本折行后
才看全 卒業セレモニー 的全部引语。

所以：**阶段A一律用本脚本 dump，不要用 read_file 直接读裸 content_ja 文件。**

用法
----
    python3 dump_ja.py <YYYY-MM-DD> <key前缀> [key前缀 ...]

数据源优先级
------------
1) ~/.hermes/workspace/l1_raw_<YYYY-MM-DD>.json —— 每日 review cron 落盘的全量
   rows（含 content_ja / format / is_long_form / title_score / content_score）。
   **优先用它**：不用重发 curl，且与当日 review 的分级口径完全一致。
2) 若该文件不存在（比如跨日批里更早的那天没留 dump），先补一次：
   curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=D&date_to=D&limit=500" \
     > ~/.hermes/workspace/l1_raw_D.json
"""
import json
import os
import sys
import textwrap

WRAP = 500  # 远低于 read_file 的截断阈值；调大反而会被 echo 回去时限行


def load(day: str):
    path = os.path.expanduser(f'~/.hermes/workspace/l1_raw_{day}.json')
    if not os.path.exists(path):
        sys.exit(f'缺 {path} —— 先按 docstring 里的 curl 命令补 dump')
    return json.load(open(path))['rows']


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    rows = load(sys.argv[1])
    for p in sys.argv[2:]:
        hits = [r for r in rows if r['key'].startswith(p)]
        if not hits:
            print(f'===== {p} NOT FOUND')
            continue
        r = hits[0]
        print(f"===== {r['key']} | {r['title']} | fmt={r['format']} lf={r['is_long_form']} "
              f"| ja_len={len(r.get('content_ja') or '')} | ts={r.get('title_score')} cs={r.get('content_score')}")
        print(f"LINK: {r.get('link')}")
        print(f"SUMMARY: {(r.get('summary') or '')[:300]}")
        print('--- content_ja ---')
        txt = (r.get('content_ja') or '').replace('\u3000', ' ')
        for line in textwrap.wrap(txt, WRAP):
            print(line)
        print('--- end ---\n')


if __name__ == '__main__':
    main()
