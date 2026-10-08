# JSON Payload vs Shell Escaping for Long Text (6/5 session)

## The Bug

When using `update.sh` to write `rewritten_content` with shell command substitution:

```bash
# ❌ BROKEN
bash update.sh <key> rewritten_content="$(cat /tmp/draft.md)"

# ❌ ALSO BROKEN
bash update.sh <key> rewritten_content="$(cat /tmp/draft.md | python3 -c 'import sys;print(sys.stdin.read().replace(chr(34),chr(92)+chr(34)).replace(chr(10),chr(92)+chr(110)))')"
```

The second version double-escapes the newlines so that `\n` is stored as literal `\\n` 
(two characters: backslash, n) instead of a real line break.

## Correct Approach

**Step 1**: Generate JSON payload with Python:
```python
import json
data = json.dumps({
    "rewritten_title": "标题（20字内）",
    "rewritten_content": open("/tmp/xhs_draft_xxx.md").read()
})
with open("/tmp/payload.json", "w") as f:
    f.write(data)
```

**Step 2**: Send with curl:
```bash
curl -s -X PUT -H "Content-Type: application/json" \
  -d @/tmp/payload.json \
  "http://192.168.0.70:5000/api/news/<完整40位key>"
```

**Step 3**: Verify in DB:
```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT substr(rewritten_content, 1, 100) FROM news WHERE key='<完整40位key>'"
```
Check that `\n` appears as real line breaks in the output (not `\\n` literal).

## Why This Happens

`json.dumps()` handles newlines and special characters correctly by encoding them as 
`\\n` *inside the JSON string* — which the API then decodes back to real `\n`.

Shell command substitution `$(...)` does NOT handle this correctly because:
1. The shell strips/rewrites the escaping
2. The double-quote nesting within single quotes gets mangled
3. The `python3 -c` subprocess adds another layer

## Quick One-liner

For rapid iteration, the whole flow can be one-linered:

```bash
python3 -c "import json; json.dump({'rewritten_title':'标题','rewritten_content':open('/tmp/draft.md').read()}, open('/tmp/payload.json','w'))" && curl -s -X PUT -H "Content-Type: application/json" -d @/tmp/payload.json "http://192.168.0.70:5000/api/news/<key>"
```

## Key Insight

`update.sh` is fine for short field writes (title, tags, related_keys, publish_mode).
For `rewritten_content`, **always use JSON file method**.
