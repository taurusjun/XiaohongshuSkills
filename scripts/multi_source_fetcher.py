#!/usr/bin/env python3
"""
多源新闻抓取器 — Multi-Source Fetcher

为小红书自动化系统提供多元化的内容源，减少对 Yahoo Japan 单一源的依赖。
每个源实现 BaseSourceFetcher 抽象基类，通过 PipelineOrchestrator 统一调度。
使用独立 SQLite 数据库，不与主系统数据混杂。

使用方式:
    python multi_source_fetcher.py --source "Natalie 音乐" --max 3
    python multi_source_fetcher.py --all --max 10
    python multi_source_fetcher.py --source "Natalie 音乐" --dry-run     # 仅抓取预览
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── 项目路径 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 独立数据库路径（从 sources.json 读取，通过配置传递）─────
_DB_PATH: str = ""  # 在 PipelineOrchestrator.__init__ 中初始化


def _get_db_path() -> str:
    """获取独立数据库的绝对路径"""
    if _DB_PATH:
        return _DB_PATH
    config = _load_source_config()
    rel = config.get("db_path", "data/multi_source.db")
    return str(PROJECT_ROOT / rel)


def _init_db(db_path: str | None = None):
    """创建独立数据库表结构（若不存在）"""
    path = db_path or _get_db_path()
    os.makedirs(Path(path).parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS news (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT UNIQUE NOT NULL,
            title       TEXT NOT NULL,
            title_ja    TEXT,
            link        TEXT NOT NULL,
            source      TEXT,
            category    TEXT,
            content     TEXT,
            comment     TEXT,
            summary     TEXT,
            tags        TEXT,
            image_url   TEXT,
            original_image_url TEXT,
            pub_time    TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            updated_at  TEXT DEFAULT (datetime('now','localtime')),
            fetch_by    TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS fetch_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            fetched     INTEGER DEFAULT 0,
            success     INTEGER DEFAULT 0,
            skip_reason TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


def _db_connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ── 统一数据模型 ───────────────────────────────────────────

@dataclass
class RawArticle:
    """每个源抓取列表页后返回的统一结构"""
    title_ja: str           # 原文章标题
    link: str               # 文章完整 URL
    source: str             # 源标识，如 'Natalie 音乐'
    key: str = ""           # 唯一 key（带源前缀），自动生成
    body_text: str = ""     # 正文内容
    image_url: str = ""     # 封面图 URL
    pub_time: str = ""      # 发布时间
    extra_tags: list[str] = field(default_factory=list)  # 附加标签

    def __post_init__(self):
        if not self.key and self.link:
            self.key = self._derive_key()

    def _derive_key(self) -> str:
        """按 source 前缀 + 文章ID 生成唯一 key"""
        raw = self.source.lower().strip()
        cleaned = re.sub(r'[\u4e00-\u9fff\s]+', '', raw)
        prefix = re.sub(r'[^a-z0-9]', '_', cleaned).strip('_') or 'source'
        m = re.search(r'/news/(\d+)', self.link)
        if m:
            return f"{prefix}_{m.group(1)}"
        m = re.search(r'/(\d+)', self.link)
        if m:
            return f"{prefix}_{m.group(1)}"
        return f"{prefix}_{hashlib.md5(self.link.encode()).hexdigest()[:12]}"


# ── 抽象基类 ───────────────────────────────────────────────

class BaseSourceFetcher(ABC):
    """所有源抓取器必须实现的接口"""

    def __init__(self, config: dict):
        self.config = config
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    @abstractmethod
    def fetch_list(self) -> list[RawArticle]:
        """抓取该源最新文章列表，返回 RawArticle 列表（可不含 body_text）"""
        pass

    @abstractmethod
    def fetch_detail(self, article: RawArticle) -> RawArticle:
        """抓取文章详情：填充 body_text / image_url / pub_time，返回完整对象"""
        pass

    def proxies(self) -> dict | None:
        """返回代理配置（子类覆盖）"""
        return None

    def extract_key(self, url: str) -> str:
        return RawArticle(source=self.source_name, link=url).key

    @property
    @abstractmethod
    def source_name(self) -> str:
        """源显示名"""
        pass

    def log(self, msg: str):
        print(f"  [{self.source_name}] {msg}")


# ── NatalieFetcher ─────────────────────────────────────────

class NatalieFetcher(BaseSourceFetcher):
    """音楽ナタリー (natalie.mu) 音乐娱乐新闻抓取器"""

    BASE_URL = "https://natalie.mu"

    @property
    def source_name(self) -> str:
        return "Natalie 音乐"

    def fetch_list(self) -> list[RawArticle]:
        """从 /music/news 列表页抓取最新文章"""
        list_url = self.config.get("list_url", f"{self.BASE_URL}/music/news")
        max_count = self.config.get("max_per_fetch", 10)

        resp = self._session.get(list_url, timeout=15, proxies=self.proxies())
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        articles: list[RawArticle] = []
        for card in soup.select("p.NA_card_title"):
            a_tag = card.find_parent("a")
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            if not href.startswith("http"):
                href = self.BASE_URL + href
            if "/news/" not in href:
                continue

            title = card.get_text(strip=True)
            if len(title) < 10:
                continue

            article = RawArticle(
                title_ja=title,
                link=href,
                source=self.source_name,
                extra_tags=self.config.get("extra_tags", []),
            )
            articles.append(article)
            if len(articles) >= max_count:
                break

        self.log(f"列表页找到 {len(articles)} 条")
        return articles

    def fetch_detail(self, article: RawArticle) -> RawArticle:
        """抓取单篇文章详情"""
        try:
            resp = self._session.get(article.link, timeout=15, proxies=self.proxies())
            resp.raise_for_status()
        except Exception as e:
            self.log(f"⚠️ 详情页抓取失败: {e}")
            return article

        soup = BeautifulSoup(resp.text, "html.parser")

        # og:image
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            article.image_url = og_img["content"]

        # 发布时间
        date_el = soup.select_one(".NA_article_date")
        if date_el:
            raw_date = date_el.get_text(strip=True)
            article.pub_time = self._parse_date(raw_date)

        # 正文
        body_el = soup.select_one(".NA_article_body")
        if body_el:
            # 只取直接子元素中的 <p> 和 <h2>，跳过所有 <div>/<section>/<aside>（嵌入文章、社交、画廊、标签等）
            paragraphs = []
            for child in body_el.find_all(recursive=False):
                if child.name not in ("p", "h2", "h3", "h4"):
                    continue
                text = child.get_text(strip=True)
                text = re.sub(r'[\s│┃]+', ' ', text).strip()
                if len(text) < 20:
                    continue
                skip_patterns = [
                    r'(広告|PR|AD)',
                ]
                if any(re.search(pt, text) for pt in skip_patterns):
                    continue
                paragraphs.append(text)

            article.body_text = "\n".join(paragraphs)
            self.log(f"正文 {len(article.body_text)} 字 ({len(paragraphs)} 段)")
        else:
            self.log("⚠️ 未找到正文")

        return article

    def _parse_date(self, raw: str) -> str:
        """将 '2026年5月29日 20:15' 转为 '2026.05.29 20:15'"""
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})?', raw)
        if m:
            y, mo, d, hm = m.groups()
            hm = hm or "00:00"
            return f"{y}.{int(mo):02d}.{int(d):02d} {hm}"
        return raw


# ── 源注册表 ───────────────────────────────────────────────

_SOURCE_REGISTRY: dict[str, type[BaseSourceFetcher]] = {
    "Natalie 音乐": NatalieFetcher,
}


def get_fetcher(source_name: str, config: dict) -> BaseSourceFetcher | None:
    cls = _SOURCE_REGISTRY.get(source_name)
    if cls:
        return cls(config)
    return None


def _load_source_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "sources.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {"sources": {}, "global": {}}


# ── 去重（基于独立数据库）─────────────────────────────────

def load_existing_keys(source: str | None = None,
                       hours_back: int = 48,
                       db_path: str | None = None) -> set[str]:
    """从独立数据库加载已有 key，用于去重"""
    path = db_path or _get_db_path()
    if not Path(path).exists():
        return set()
    try:
        conn = _db_connect(path)
        if source:
            rows = conn.execute(
                "SELECT key FROM news WHERE created_at >= datetime('now', ?) AND fetch_by = ?",
                (f"-{hours_back} hours", source)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key FROM news WHERE created_at >= datetime('now', ?)",
                (f"-{hours_back} hours",)
            ).fetchall()
        conn.close()
        return {r['key'] for r in rows}
    except Exception as e:
        print(f"  ⚠️ 加载已有 key 失败: {e}")
        return set()


# ── AI 摘要（调用 process_news_item）──────────────────────

def call_process_news_item(news_dict: dict, keyword: str = "") -> dict:
    """调用 process_news_item 进行 AI 翻译/评分，注入独立 DB 路径"""
    # 临时注入独立 DB 路径，让 process_news_item 写入正确位置
    os.environ["MULTI_SOURCE_DB"] = _DB_PATH or _get_db_path()
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from yahoo_common import process_news_item
    return process_news_item(news_dict, keyword=keyword)


# ── PipelineOrchestrator ───────────────────────────────────

class PipelineOrchestrator:
    """多源调度 + 去重 + 写入独立数据库"""

    def __init__(self, config: dict):
        global _DB_PATH
        self.config = config
        self.sources_config = config.get("sources", {})
        # 初始化全局 DB 路径
        rel = config.get("db_path", "data/multi_source.db")
        _DB_PATH = str(PROJECT_ROOT / rel)
        self.db_path = _DB_PATH
        # 确保库已初始化
        _init_db(self.db_path)

    def run_source(self, source_name: str, max_count: int = 5,
                   dry_run: bool = False) -> list[dict]:
        source_cfg = self.sources_config.get(source_name)
        if not source_cfg or not source_cfg.get("enabled", True):
            print(f"⏭️ 跳过 {source_name}（未启用或未配置）")
            return []

        fetcher = get_fetcher(source_name, source_cfg)
        if not fetcher:
            print(f"❌ 不支持的内容源: {source_name}")
            return []

        print(f"\n{'━' * 50}")
        print(f"🔍 {source_name}")
        print(f"{'━' * 50}")

        try:
            articles = fetcher.fetch_list()
        except Exception as e:
            print(f"  ❌ 列表页抓取失败: {e}")
            return []

        if not articles:
            print("  ⏭️ 列表为空")
            return []

        existing_keys = load_existing_keys(source=source_name, db_path=self.db_path)
        fresh = [a for a in articles if a.key not in existing_keys]
        print(f"  📊 去重: 总共 {len(articles)}, 新 {len(fresh)}, 已存在 {len(articles) - len(fresh)}")

        if not fresh:
            return []

        results = []
        for i, article in enumerate(fresh[:max_count]):
            print(f"\n  [{i + 1}/{min(len(fresh), max_count)}] {article.title_ja[:50]}...")

            try:
                article = fetcher.fetch_detail(article)
            except Exception as e:
                print(f"    ⚠️ 详情页抓取失败: {e}")
                continue

            if not article.body_text or len(article.body_text) < 50:
                print(f"    ⏭️ 正文过短，跳过")
                continue

            if dry_run:
                print(f"    🔍 key={article.key}")
                print(f"    📎 {article.link}")
                print(f"    🖼️ image={article.image_url}")
                print(f"    🕐 pub_time={article.pub_time}")
                print(f"    📝 正文 ({len(article.body_text)}字):")
                words = article.body_text[:500]
                for line in words.split('\n'):
                    print(f"      {line}")
                results.append({'key': article.key, 'title_ja': article.title_ja})
                continue

            # 写入独立数据库
            self._db_insert_article(article)
            print(f"    ✅ 已入库 | key={article.key} | {article.title_ja[:40]}")
            results.append({'key': article.key, 'title_ja': article.title_ja})

        print(f"\n  ✅ {source_name} 完成: 成功 {len(results)} 条")
        return results

    def run_all(self, max_per_source: int = 5) -> dict[str, list[dict]]:
        results = {}
        for name, cfg in self.sources_config.items():
            if cfg.get("enabled", True):
                results[name] = self.run_source(name, max_count=max_per_source)
        return results

    def _db_insert_article(self, article: RawArticle):
        """写入独立数据库"""
        try:
            conn = _db_connect(self.db_path)
            conn.execute(
                """INSERT OR IGNORE INTO news
                   (key, title, title_ja, link, source, content, image_url,
                    original_image_url, pub_time, fetch_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (article.key, article.title_ja, article.title_ja,
                 article.link, article.source, article.body_text,
                 article.image_url or '', article.image_url or '',
                 article.pub_time or '', article.source)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"    ❌ 入库失败: {e}")


# ── CLI 入口 ───────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="多源新闻抓取器 — 独立数据库")
    parser.add_argument('--source', type=str, default='',
                        help='指定源名（如 "Natalie 音乐"）')
    parser.add_argument('--all', action='store_true',
                        help='运行所有启用的源')
    parser.add_argument('--max', type=int, default=5,
                        help='每个源最多处理条数')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅抓取并打印原始数据，不写入数据库')
    args = parser.parse_args()

    config = _load_source_config()
    orchestrator = PipelineOrchestrator(config)

    if args.source:
        orchestrator.run_source(args.source, max_count=args.max, dry_run=args.dry_run)
    elif args.all:
        orchestrator.run_all(max_per_source=args.max)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
