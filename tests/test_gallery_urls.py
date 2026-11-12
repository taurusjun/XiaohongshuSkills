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


def test_numero_interview_supported():
    """numero.jp 访谈页是单段路径 /interviewNNN/，且图片是 data-src-img 懒加载。"""
    from scripts.gallery_fetch import GALLERY_SITES, is_gallery_url
    assert "numero.jp" in GALLERY_SITES
    assert is_gallery_url("https://numero.jp/interview509/")
    assert is_gallery_url("https://numero.jp/interview123/")
    assert not is_gallery_url("https://numero.jp/magazine/")


def test_maquia_photo_supported():
    """maquia.hpplus.jp 的 /photo/ 页是 Fastly 挑战页，需 CDP 渲染。"""
    from scripts.gallery_fetch import GALLERY_SITES, is_gallery_url
    assert "maquia.hpplus.jp" in GALLERY_SITES
    assert is_gallery_url("https://maquia.hpplus.jp/makeup/photo/113456/")


def test_dailyshincho_photo_supported():
    """dailyshincho.jp 图集只在 ?photo=N 页面出现，且当前选中图在 og:image。"""
    from scripts.gallery_fetch import GALLERY_SITES, is_gallery_url
    assert "dailyshincho.jp" in GALLERY_SITES
    assert is_gallery_url("https://www.dailyshincho.jp/article/2026/11112200/?photo=2")
