"""图集 URL 识别回归用例（生产侧最小集）。"""


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
