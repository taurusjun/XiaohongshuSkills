# 发布口径：待发布正文 ≤ 900 字左右

⚠️ 写稿阶段「story 正文不设上限、以密度为准」仍然有效；但**挂入待发布队列（`publish_xhs=1`）的稿子，正文要压到约 900 字（880–910）**。

用户 10/5 指令：「待发布超过900字的都需要改成900字左右」。

- 写稿时不必压，**挂队列前压**。
- 压缩＝编辑性压缩（保留骨架/引语/时间线，砍冗余），不是重写；改标题、换叙事线都是错的。
- 压完必须重跑完整闭环：`batch_precheck.py` → `renwei-pre-commit.py` → `sqlite3 UPDATE`（保留 `publish_xhs`/`publish_mode`/`related_keys`）→ `score_dims` 覆盖落盘。
- length() 含换行符，xhs 字数 = `len(rc) - rc.count('\n')`。
- 深访类（ja 3k+）压到 900 会跌破密度 30%——用户指定字数优先，在 score_dims 理由里注明即可，不要加字回去。

命令级流程、坑与台账见 `xhs-publish-workflow` 的 `references/trim-publish-queue-to-900.md`（SKILL.md 另有「待发布队列字数上限」一节）。
