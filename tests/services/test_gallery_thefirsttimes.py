"""thefirsttimes 图集抓取：/report/ 路径 + 附件页回退 + 去重（回归）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import gallery_fetch as gf  # noqa: E402


class _R:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


def test_thefirsttimes_report_path_and_dedup(monkeypatch, capsys):
    art = '<a href="/report/0000896352/attachment/20261008-st-190802/">x</a>'
    page = ('<img src="https://www.thefirsttimes.jp/admin/wp-content/uploads/2026/10/'
            '20261008-st-190802.jpg"><img src="https://www.thefirsttimes.jp/admin/wp-content/uploads/'
            '2026/10/20261008-st-190802.jpg">')

    def fake_get(url, headers, timeout=15):
        return _R(art if "attachment/" not in url else page)

    monkeypatch.setattr(gf, "_tft_get", fake_get)
    imgs = gf._scrape_thefirsttimes(
        "https://www.thefirsttimes.jp/report/0000896352/attachment/20261008-st-190802/")
    assert imgs == ["https://www.thefirsttimes.jp/admin/wp-content/uploads/2026/10/20261008-st-190802.jpg"]


def test_thefirsttimes_article_failure_falls_back(monkeypatch):
    page = '<img src="https://www.thefirsttimes.jp/admin/wp-content/uploads/2026/10/x.jpg">'

    def fake_get(url, headers, timeout=15):
        if "attachment/" not in url:
            raise RuntimeError("proxy down")     # 文章页抓取失败
        return _R(page)                          # 回退直抓附件页

    monkeypatch.setattr(gf, "_tft_get", fake_get)
    imgs = gf._scrape_thefirsttimes(
        "https://www.thefirsttimes.jp/report/0000896352/attachment/20261008-st-190802/")
    assert imgs == ["https://www.thefirsttimes.jp/admin/wp-content/uploads/2026/10/x.jpg"]
