# 写稿前必须读daily review存档文件（7/8教训）

## 问题

用户说"根据今日review结果文件，启动写稿skill"时，第一动作不是搜session、不是跑API全量、不是看renwei文件——而是直接读 `~/.hermes/daily-reviews/` 目录下的日期命名md文件。

## 文件位置

存档路径：`~/.hermes/daily-reviews/YYYY-MM-DD.md`

文件名格式：`YYYY-MM-DD.md`（如 `2026-07-08.md`）

## 如何找到今天的文件

1. 先查看目录下有哪些文件：`ls ~/.hermes/daily-reviews/`
2. 今天日期文件：`~/.hermes/daily-reviews/$(date +%Y-%m-%d).md`
3. 直接用 `read_file(path='~/.hermes/daily-reviews/YYYY-MM-DD.md')`

## 文件结构

review文件包含：
- 第1层：全量素材扫描 + 聚类 + S/A/B/C分级结果
- 第2层：S级价值建议
- 第3层：跨时间关联分析
- 第4层：发布数据回顾

写稿阶段主要消费第1层的分级结果（S/A/AKB大TOP的key列表）。

## 禁止

- ❌ 在DB里自己翻ts排序试图脑补分级结果
- ❌ 搜session历史找review
- ❌ 看renwei文件以为那是review结果
- ❌ 从workspace找旧review存档

找不到文件时，问用户「今天是哪天的review」。
