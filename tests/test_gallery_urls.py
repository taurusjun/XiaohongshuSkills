"""图集 URL 归一化 / 站点支持（回归测试）。"""


def test_oricon_embed_normalized():
    from scripts.gallery_fetch import normalize_gallery_url
    u = "https://www.oricon.co.jp/news/2489906/embed/photo/?anc=337&utm_source=x"
    assert normalize_gallery_url(u) == "https://www.oricon.co.jp/news/2489906/photo/1/"


def test_normalize_keeps_other_urls():
    from scripts.gallery_fetch import normalize_gallery_url
    for u in ("https://www.oricon.co.jp/news/2489906/photo/1/",
              "https://natalie.mu/eiga/gallery/news/695393/media/140128"):
        assert normalize_gallery_url(u) == u


def test_tokyo_sports_supported():
    from scripts.gallery_fetch import GALLERY_SITES, is_gallery_url
    assert "tokyo-sports.co.jp" in GALLERY_SITES
    assert is_gallery_url("https://www.tokyo-sports.co.jp/articles/photo/405080")


def test_oricon_embed_recognized_as_gallery():
    """embed 形式在归一化后应被认作图集 URL。"""
    from scripts.gallery_fetch import is_gallery_url, normalize_gallery_url
    u = normalize_gallery_url("https://www.oricon.co.jp/news/2489906/embed/photo/?anc=337")
    assert is_gallery_url(u)


def test_domain_of_does_not_mangle_subdomains():
    """lstrip("www.") 会按字符集吃字符：wpb.shueisha.co.jp → pb.shueisha.co.jp。"""
    from scripts.gallery_fetch import _domain_of
    assert _domain_of("https://wpb.shueisha.co.jp/photo/1") == "wpb.shueisha.co.jp"
    assert _domain_of("https://www.tokyo-sports.co.jp/articles/-/1") == "tokyo-sports.co.jp"
    assert _domain_of("https://web.gekisaka.jp/news/1") == "web.gekisaka.jp"


def test_sponichi_extra_path_hint():
    from scripts.gallery_fetch import GALLERY_EXTRA_PATH_HINTS
    assert "/articles/" in GALLERY_EXTRA_PATH_HINTS.get("sponichi.co.jp", ())


def test_wpb_shueisha_supported():
    from scripts.gallery_fetch import GALLERY_SITES
    assert "wpb.shueisha.co.jp" in GALLERY_SITES
