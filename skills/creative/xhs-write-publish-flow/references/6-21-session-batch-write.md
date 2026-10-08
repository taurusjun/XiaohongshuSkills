# Session 6/21: Batch write lessons & key verification

## Key inconsistency: API keys vs DB keys

When batch-writing multiple articles, API PUT can return `{"ok":true}` but the data doesn't write if the key used is a mismatched hash (different suffix from what's in the DB). This happened on 6/21 with 4 out of 10 articles.

**Fix:** Always verify ALL articles after batch write with `sqlite3 SELECT LENGTH(rewritten_content)` before declaring success. Don't trust `{"ok":true}`.

## Short-form vs long-form routing in batch mode

When processing 10+ articles in batch, not every A/B-grade article needs a full 900-word story. Route by content_ja length and story arc depth:
- **content_ja < 800字 + 无故事线** → 只设preselected=1，不动rewritten
- **content_ja ≥ 800字或有完整故事线** → 写短news或长文
- 强行把天然短素材撑到900字只会灌水

## Batch write order

Priority: S级长文 → A级短news → B级标记 → 公众号。每条写完后立即renwei+入库+验证三步走，不要等全部写完才验证。

## User preference: no "要开始吗"

After review is delivered and user says "开始" or "开始写稿", immediately transition to writing without asking which ones or whether to start. Just execute.
