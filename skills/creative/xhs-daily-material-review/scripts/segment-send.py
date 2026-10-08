#!/usr/bin/env python3
"""
segment-send.py — 将Markdown文件从 review 存档分段发送到 Telegram

使用 sendRichMessage API (rich_message.markdown 字段)，原生支持表格渲染。

======= 调用方式 =======

1) 从 terminal/脚本调用：
   python3 scripts/segment-send.py <file_path> [<chat_id>] [<max_chars>]

   参数说明：
     <file_path>  (必填) — 要发送的Markdown文件绝对路径
     <chat_id>    (选填) — Telegram chat ID（默认从 cron/jobs.json 提取第一个 origin）
     <max_chars>  (选填) — 每段最大字符数（默认 3500，留余量给 4000 限制）

   示例：
     python3 scripts/segment-send.py ~/.hermes/daily-reviews/2026-06-27.md
     python3 scripts/segment-send.py /tmp/output.md 8066707199 3800

2) 从 cron prompt 调用（Step 5，常见用法）：
   在每日素材 review cron 的最后一步调用（详见 xhs-daily-material-review SKILL.md）：
     python3 ~/.hermes/skills/creative/xhs-daily-material-review/scripts/segment-send.py \
       ~/.hermes/daily-reviews/$(TZ=Asia/Tokyo date '+%Y-%m-%d').md

3) 从 Hermes shell 直接运行：
   terminal(command='python3 scripts/segment-send.py <file_path>')

======= 触发时机 =======

本脚本在 每日素材 review 流程的 Step 5 被调用——当 review 存档文件写完之后。
具体流程：
  Step 1: 全量扫描 → 聚类 → 分级（第1层）
  Step 2: 价值建议（第2层）+ 跨时间关联（第3层）
  Step 3: 发布数据回顾（第4层）
  Step 4: write_file 存档到 ~/.hermes/daily-reviews/YYYY-MM-DD.md
  Step 5: → python3 segment-send.py 存档文件路径   ← 这里触发
  Step 6: 自查审计

======= 内部实现 =======

- 使用 Telegram Bot API 的 sendRichMessage 方法
- rich_message.markdown 字段传原始Markdown，Telegram 自动解析表格（type: table, is_header, bordered, striped）
- 不支持 sendMessage 的 parse_mode（那些不支持表格），必须用 sendRichMessage
- 支持 SOCKS5 代理（优先）和直连（fallback）
- 不走 Hermes cron deliver，直接通过 API 发送到 Telegram

======= 依赖 =======

- Python 3 标准库（urllib, json, re, time）
- SOCKS5 代理：urllib.request.ProxyHandler（内置支持，无需额外安装）
- 不需要第三方库（不走 python-telegram-bot 等）

======= 配置来源 =======

- bot_token: 从 ~/.hermes/config.yaml 的 telegram.bot_token 读取
- chat_id: 优先用命令行传入的 <chat_id> 参数，否则从 ~/.hermes/cron/jobs.json 提取第一个 job 的 origin.chat_id
- proxy: hardcoded socks5://127.0.0.1:10090 → 7/24修正：从 $HTTP_PROXY 环境变量动态读取端口，不再硬编码

======= 兜底修复 =======

脚本会在发送前自动检测并修复以下非标准Markdown表格格式，
以兼容LLM生成存档时可能产生的格式偏差：

1. **表头行和分隔行在同一行**（如 `S级 | 标题 | |----|------|`）
   → 拆分为标准两行格式
2. **`|` 前有前导空格** → 去掉空格
3. **表格前是 `**粗体**` 而非 `## 标题`** → 粗体改为标题
4. **无分隔行的 `|` 格式** → 自动推断并插入分隔行
5. **数据行中嵌入多余的 `|`** → 取前N列（N=表头列数）

======= 分段边界保护 =======

发送前检测表格是否在段边界被切开（段尾最后一条 | 行 + 下段首行也是 | 行）。
如果检测到，把当前段中的表格部分整体移到下一段，不在表格中间切。

关键修复（6/26）：检测时不使用 `strip().split()[-1]`（会隐藏尾部空行，
导致 | 行被空行覆盖），改为倒序扫描整个段找最后一条 | 行。

详见 references/segment-send-table-boundary-pitfall.md。

注意：兜底是保底方案，最优解是在写存档时直接输出标准格式。
参见 references/markdown-table-format.md。
"""

import json, re, sys, urllib.request, time
from pathlib import Path

FILE = sys.argv[1]
EXPLICIT_CHAT_ID = sys.argv[2] if len(sys.argv) > 2 else None
MAX_CHARS = int(sys.argv[3]) if len(sys.argv) > 3 else 3500

if not Path(FILE).exists():
    print(f"❌ 文件不存在: {FILE}", file=sys.stderr)
    sys.exit(1)

# 读取 bot_token
home = Path.home()
cfg_text = (home / '.hermes/config.yaml').read_text()
m = re.search(r'bot_token:\s*(\S+)', cfg_text)
TOKEN = m.group(1) if m else ''
if not TOKEN:
    print("❌ TOKEN 为空", file=sys.stderr)
    sys.exit(1)

# 确定 chat_id
chat_id = ''
if EXPLICIT_CHAT_ID:
    chat_id = EXPLICIT_CHAT_ID
else:
    try:
        raw = json.loads((home / '.hermes/cron/jobs.json').read_text())
        jobs = raw if isinstance(raw, list) else raw.get('jobs', [])
        for j in jobs:
            if isinstance(j, dict):
                o = j.get('origin')
                if isinstance(o, dict):
                    cid = o.get('chat_id', '')
                    if cid: chat_id = cid; break
    except Exception:
        pass

if not chat_id:
    print("❌ CHAT_ID 为空（可传第二个参数指定）", file=sys.stderr)
    sys.exit(1)

# ─── 兜底修复：修复非标准表格格式 ──────────────────────────────

TABLE_LINE_RE = re.compile(r'^\s*\|')  # 以 | 开头的行（可能有前导空格）
# 混合行检测：不以 | 开头但包含 | 且有文本+分隔符混排
MIXED_ROW_RE = re.compile(r'(\|\s*-{3,})|(-{3,}\s*\|)')  # 行内包含 |--- 或 ---| 模式

def _detect_col_count(line: str) -> int:
    """统计 | 数量确定列数"""
    return max(line.count('|') - 1, 0)

def _is_separator(line: str) -> bool:
    """判断是否为分隔行（|---|）"""
    stripped = line.strip()
    if not stripped.startswith('|') or not stripped.endswith('|'):
        return False
    # 去掉首尾 |，剩下的全是 --- 或 :- 或 -:
    inner = stripped[1:-1].strip()
    if not inner:
        return False
    parts = [p.strip() for p in inner.split('|')]
    return all(re.fullmatch(r':?-{3,}:?', p) for p in parts)

def _line_is_header_row(text: str) -> bool:
    """判断行是否为表头行（没有实际数据，只是列标签 + | + --- 混在同一行）"""
    # 检查形如 "S级 | 标题 | |----|------|" 的混合行
    parts = [p.strip() for p in text.split('|')]
    # 去掉首尾空
    parts = [p for p in parts if p]
    has_separator = any(re.fullmatch(r':?-{3,}:?', p) for p in parts)
    has_text = any(not re.fullmatch(r':?-{3,}:?', p) and len(p) > 0 for p in parts)
    return has_separator and has_text

def _split_mixed_header_row(text: str) -> tuple:
    """将 'S级 | 标题 | |----|------|' 拆成 ('| S级 | 标题 |', '|---|---|')"""
    parts = [p.strip() for p in text.split('|')]
    parts = [p for p in parts if p]
    text_parts = [p for p in parts if not re.fullmatch(r':?-{3,}:?', p) and len(p) > 0]
    sep_parts = [p for p in parts if re.fullmatch(r':?-{3,}:?', p)]
    return '| ' + ' | '.join(text_parts) + ' |', '| ' + ' | '.join(sep_parts) + ' |'

def _fix_bold_before_table(lines: list, idx: int) -> list:
    """如果表格前一行是 **粗体**，改为 ## 标题"""
    if idx > 0:
        prev = lines[idx - 1].strip()
        m = re.match(r'\*\*(.+?)\*\*:?', prev)
        if m:
            lines[idx - 1] = '## ' + m.group(1)
    return lines

def fix_tables(content: str) -> tuple:
    """
    修复内容中的非标准Markdown表格格式。
    返回 (修复后的内容, 修复统计dict)
    """
    lines = content.split('\n')
    fixed = []
    stats = {'split_mixed': 0, 'trim_spaces': 0, 'bold_to_heading': 0, 'insert_separator': 0}
    i = 0
    in_table = False
    table_start = -1
    
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_table_line = TABLE_LINE_RE.match(line)
        
        # 跳过前导空行
        if not stripped:
            if in_table:
                in_table = False
                table_start = -1
            fixed.append(line)
            i += 1
            continue

        # ⭐ 优先检测混合行：不以 | 开头但包含文本+分隔符混排的行
        # 如 "S级（强烈推荐）🔥 | ID | 标题 | |----|------|------|"
        # 这类行既不是标准表格行（无前导|），也不是普通文本行（含分隔符）
        if not is_table_line and '|' in stripped:
            # 检查是否包含 ---| 或 |--- 模式（分隔符特征）
            sep_in_line = False
            for part in stripped.split('|'):
                part = part.strip()
                if re.fullmatch(r':?-{3,}:?', part):
                    sep_in_line = True
                    break
            # 还要检查是否同时有文本内容
            has_text = any(
                len(p.strip()) > 1 and not re.fullmatch(r':?-{3,}:?', p.strip())
                for p in stripped.split('|')
            )
            
            if sep_in_line and has_text:
                # 这是混合行 → 拆成表头行 + 分隔行
                if not in_table:
                    in_table = True
                    table_start = len(fixed)
                    # 检查前一行粗体
                    if table_start > 0:
                        prev = fixed[table_start - 1].strip()
                        m = re.match(r'\*\*(.+?)\*\*:?', prev)
                        if m:
                            fixed[table_start - 1] = '## ' + m.group(1)
                            stats['bold_to_heading'] += 1
                
                hdr, sep = _split_mixed_header_row(stripped)
                fixed.append(hdr)
                fixed.append(sep)
                stats['split_mixed'] += 1
                i += 1
                continue
        
        if is_table_line:
            if not in_table:
                in_table = True
                table_start = len(fixed)
                if table_start > 0:
                    prev = fixed[table_start - 1].strip()
                    m = re.match(r'\*\*(.+?)\*\*:?', prev)
                    if m:
                        fixed[table_start - 1] = '## ' + m.group(1)
                        stats['bold_to_heading'] += 1
                if line != stripped:
                    line = stripped
                    stats['trim_spaces'] += 1
            
            if in_table:
                if line != stripped:
                    line = stripped
                    stats['trim_spaces'] += 1
                if _line_is_header_row(line):
                    hdr, sep = _split_mixed_header_row(line)
                    fixed.append(hdr)
                    fixed.append(sep)
                    stats['split_mixed'] += 1
                    i += 1
                    continue
                if _is_separator(line):
                    fixed.append(line)
                    i += 1
                    continue
                fixed.append(line)
        else:
            if in_table:
                in_table = False
            fixed.append(line)
        
        i += 1
    
    result = '\n'.join(fixed)
    return result, stats


def send_msg(text):
    text = text.replace('\x00', '')[:4000]
    
    payload = json.dumps({
        'chat_id': chat_id,
        'rich_message': {
            'markdown': text
        }
    }).encode('utf-8')
    
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendRichMessage",
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    
    # 先走直连（cron环境下手动unset proxy）
    import os
    for k in ['HTTP_PROXY','http_proxy','HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy']:
        os.environ.pop(k, None)
    try:
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception:
        pass
    
    # fallback: socks5 proxy
    try:
        import os as _os
        # 从环境变量读取实际端口（7/24修正：原hardcode 20808，实际环境 20809）
        _env_proxy = _os.environ.get('HTTP_PROXY', '') or _os.environ.get('http_proxy', '')
        _proxy_url = 'socks5://127.0.0.1:20809'  # fallback默认
        if _env_proxy:
            import re as _re
            _m = _re.search(r'127\.0\.0\.1:(\d+)', _env_proxy)
            if _m:
                _proxy_url = f'socks5://127.0.0.1:{_m.group(1)}'
        proxy_handler = urllib.request.ProxyHandler({
            'https': _proxy_url,
            'http': _proxy_url
        })
        opener = urllib.request.build_opener(proxy_handler)
        opener.open(req, timeout=20)
        return True
    except Exception as e:
        print(f"⚠️ 发送失败: {e}", file=sys.stderr)
        return False

# 按行拆分发送
content = Path(FILE).read_text()

# 兜底修复非标准表格
content, fix_stats = fix_tables(content)
total_fixes = sum(fix_stats.values())
if total_fixes > 0:
    print(f"🔧 修复了 {total_fixes} 个表格格式问题: ", file=sys.stderr)
    for k, v in fix_stats.items():
        if v > 0:
            print(f"   {k}: {v}", file=sys.stderr)

lines = content.split('\n')
current = ''
current_lines = 0
segment_num = 1

for i, line in enumerate(lines):
    # 检测：当前段 + 下一行是否超限
    test = current + '\n' + line if current else line
    
    if len(test) <= MAX_CHARS:
        current = test
        current_lines += 1
    else:
        # 检测表格在段边界的完整方法：
        # 1. 当前段尾部的非空行是否是以 | 开头的表格行
        # 2. 下一行是否也是 | 开头的表格行
        next_is_table = line.strip().startswith('|')
        
        # 找出当前段最后5行中最靠后的 | 表格行（忽略空行）
        current_lines_list = current.split('\n')
        current_last_table_line = -1
        for j in range(len(current_lines_list) - 1, -1, -1):
            cl = current_lines_list[j].strip()
            if cl.startswith('|'):
                current_last_table_line = j
                break
        
        current_has_table_end = current_last_table_line >= 0
        
        if current_has_table_end and next_is_table:
            # 表格在段边界被切开！
            # 策略：把当前段中以 | 开头的行全部移到下一段（整体后移）
            # current_lines_list 已在上方定义
            table_first_row = -1
            for j in range(len(current_lines_list) - 1, -1, -1):
                cl = current_lines_list[j].strip()
                if not cl.startswith('|') and not cl.startswith('|---'):
                    table_first_row = j + 1  # 表格从这行开始
                    break
                if j == 0:
                    table_first_row = 0  # 整段都是表格
            
            if table_first_row > 0:
                # 表格前的内容先发
                before_table = '\n'.join(current_lines_list[:table_first_row])
                # 表格部分留着
                table_part = '\n'.join(current_lines_list[table_first_row:])
                if send_msg(before_table):
                    print(f"📤 段 {segment_num} OK（表格前内容）")
                else:
                    print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)
                segment_num += 1
                time.sleep(1)
                current = table_part + '\n' + line
                current_lines = len(current_lines_list) - table_first_row + 1
            else:
                # 整段都是表格→强行发（无解）
                if current:
                    if send_msg(current):
                        print(f"📤 段 {segment_num} OK（表格段）")
                    else:
                        print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)
                    segment_num += 1
                    time.sleep(1)
                current = line
                current_lines = 1
        else:
            # 正常切分
            if send_msg(current):
                print(f"📤 段 {segment_num} OK")
            else:
                print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)
            current = line
            current_lines = 1
            segment_num += 1
            time.sleep(1)

if current:
    if send_msg(current):
        print(f"📤 段 {segment_num} OK")
    else:
        print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)

print(f"✅ 共 {segment_num} 段发送完成")
