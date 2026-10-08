"""特征化：cdp_publish 常量（拆分后值不变）。"""
import xhs_constants as c


def test_urls():
    assert c.XHS_CREATOR_LOGIN_CHECK_URL == "https://creator.xiaohongshu.com"
    assert c.XHS_HOME_URL == "https://www.xiaohongshu.com"
    assert c.CDP_PORT == 9222


def test_selectors_keys():
    for k in ("image_text_tab", "upload_input", "title_input", "content_editor",
              "publish_button", "schedule_datetime_input"):
        assert k in c.SELECTORS


def test_login_cache_file_points_to_tmp():
    assert c.LOGIN_CACHE_FILE.endswith("tmp/login_status_cache.json")


def test_cdp_publish_reexports_constants():
    import cdp_publish as cp
    assert cp.XHS_CREATOR_LOGIN_CHECK_URL == c.XHS_CREATOR_LOGIN_CHECK_URL
    assert cp.SELECTORS is c.SELECTORS
