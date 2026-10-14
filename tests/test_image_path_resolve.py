"""图片路径配置化：DB 里迁移来的绝对路径按当前 GALLERY_CACHE_DIR 归一化。"""
from config import yahoo_conf as y


def test_resolve_migrated_abs_path(monkeypatch, tmp_path):
    monkeypatch.setattr(y, "GALLERY_CACHE_DIR", str(tmp_path))
    key = "cd62bbe1ea226f267b898b6edb147177d20ed885"
    (tmp_path / key).mkdir()
    (tmp_path / key / "cover.jpg").write_bytes(b"x")
    # 宿主路径（迁移进容器后失效）→ 归一化到当前缓存目录
    got = y.resolve_image_path(f"/Users/user/.cache/xhs_images/{key}/cover.jpg")
    assert got == str(tmp_path / key / "cover.jpg")


def test_resolve_existing_and_http(monkeypatch, tmp_path):
    monkeypatch.setattr(y, "GALLERY_CACHE_DIR", str(tmp_path))
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    assert y.resolve_image_path(str(f)) == str(f)            # 已存在 → 原样
    assert y.resolve_image_path("https://x/y.jpg") == "https://x/y.jpg"   # 远程 → 原样
    assert y.resolve_image_path("") == ""                    # 空 → 空
