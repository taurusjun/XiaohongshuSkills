"""图集 URL 识别回归用例（生产侧最小集）。"""


def test_numero_interview_supported():
    """numero.jp 访谈页是单段路径 /interviewNNN/，且图片是 data-src-img 懒加载。"""
    from scripts.gallery_fetch import GALLERY_SITES, is_gallery_url
    assert "numero.jp" in GALLERY_SITES
    assert is_gallery_url("https://numero.jp/interview509/")
    assert is_gallery_url("https://numero.jp/interview123/")
    assert not is_gallery_url("https://numero.jp/magazine/")
