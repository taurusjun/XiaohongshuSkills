## ADDED Requirements

- Phase 2 LLM 规划输出必须为每个话题包含 `angle` 字段（一句话内容切入角度）
- `TopicQuota` dataclass 包含 `angle: str = ""` 字段，默认空字符串
- angle 通过 keyword dict（`{"keyword":..., "max":..., "angle":...}`）传递到 Phase 3
- 相同 Yahoo 搜索词的多个话题在 Phase 3 合并，取较大 max，保留非空 angle
- `news['_angle']` 在 `fetch_all_articles()` 中设置，包括 retry 路径
- `generate_story_article()` 和 `generate_content_and_comment()` 接收 `angle: str = ""` 参数
- 当 angle 非空时，在 prompt 中注入 `【内容角度】今日重点关注：{angle}`
- angle 为空时不注入，行为与原来完全一致（向后兼容）
