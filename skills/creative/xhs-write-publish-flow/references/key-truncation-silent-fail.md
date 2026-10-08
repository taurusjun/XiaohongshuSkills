# Key Truncation Silent Failure (6/5 session)

## The Bug
When using `update.sh` or `curl PUT /api/news/<key>` to write `related_keys`, the key values
are truncated to 16 characters instead of the full 40-character SHA1 hash.

Example:
- Written: `ref_keys="d9ae412ca3fa0ea2,80c61a17614a2ad1"`
- Actually stored: `d9ae412ca3fa0ea2,80c61a17614a2ad1` (16-char prefixes)
- But these are NOT valid keys in the database

## The Result
`{"ok": true}` is returned for EVERY call, even non-existent fields and truncated keys.
This is a **silent failure** — the API does not validate that:
1. The field name exists in the DB schema (`ref_keys` vs `related_keys`)
2. The key values are valid 40-char SHA1 hashes
3. The referenced keys actually exist in the news table

## Demonstration
```bash
# This returns {"ok": true} but writes NOTHING
bash update.sh <key> ref_keys="abc123def456,ghi789jkl012"
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT ref_keys FROM news WHERE key='<key>'"
# Returns empty — field ref_keys doesn't exist
```

```bash
# Same silent success with truncated keys
bash update.sh <key> related_keys="2e0ad9b77fbcbd13"  # only 16 chars
# Returns {"ok": true}
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT related_keys FROM news WHERE key='<key>'"
# Shows "" — the 16-char key was NOT stored because the full key doesn't match
```

## Lesson
Always verify writes with `sqlite3` SELECT after every PUT, especially when:
- Writing to a new field name (check .schema first!)
- Writing truncated key values (always use full 40-char SHA1)
- Any write that returns {"ok": true} quickly with no apparent validation
