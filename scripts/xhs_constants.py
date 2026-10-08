"""小红书 CDP 常量/选择器 —— 从 cdp_publish.py 抽出（P6 拆分第 1 步，纯数据）。

改版排查时优先看这里的 SELECTORS。
"""
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

XHS_CREATOR_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
XHS_HOME_URL = "https://www.xiaohongshu.com"
XHS_NOTIFICATION_URL = "https://www.xiaohongshu.com/notification"
XHS_CREATOR_LOGIN_CHECK_URL = "https://creator.xiaohongshu.com"
XHS_HOME_LOGIN_MODAL_KEYWORD = "登录后推荐更懂你的笔记"
XHS_CONTENT_DATA_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
XHS_CONTENT_DATA_API_PATH = "/api/galaxy/creator/datacenter/note/analyze/list"
XHS_NOTIFICATION_MENTIONS_API_PATH = "/api/sns/web/v1/you/mentions"
XHS_SEARCH_RECOMMEND_API_PATH = "/api/sns/web/v1/search/recommend"
XHS_FEED_INACCESSIBLE_KEYWORDS = (
    "当前笔记暂时无法浏览", "你访问的页面不见了", "该内容因违规已被删除",
    "该笔记已被删除", "内容不存在", "笔记不存在", "已失效", "私密笔记",
    "仅作者可见", "因用户设置，你无法查看", "因违规无法查看",
)

SELECTORS = {
    "image_text_tab": "div.creator-tab",
    "image_text_tab_text": "上传图文",
    "video_tab": "div.creator-tab",
    "video_tab_text": "上传视频",
    "upload_input": ".upload-input",
    "upload_input_alt": 'input[type="file"]',
    "title_input": "div.d-input input",
    "title_input_alt": 'input[placeholder*="填写标题"], input[placeholder*="标题"], input.d-text',
    "content_editor": "div.tiptap.ProseMirror",
    "content_editor_alt": 'div.ProseMirror[contenteditable="true"]',
    "content_editor_alt2": "div.ql-editor",
    "content_placeholder_text": "输入正文描述",
    "publish_button": "xhs-publish-btn",
    "publish_button_legacy": "button.ce-btn.bg-red, .publish-page-publish-btn button.bg-red",
    "publish_button_text": "发布",
    "schedule_publish_button_text": "定时发布",
    "schedule_switch": ".post-time-wrapper .d-switch",
    "schedule_datetime_input": ".date-picker-container input",
    "image_preview_items": ".img-preview-area .pr",
    "login_indicator": '.user-info, .creator-header, [class*="user"]',
}

PAGE_LOAD_WAIT = 3
TAB_CLICK_WAIT = 2
UPLOAD_WAIT = 6
VIDEO_PROCESS_TIMEOUT = 120
VIDEO_PROCESS_POLL = 3
ACTION_INTERVAL = 1
MAX_TIMING_JITTER_RATIO = 0.7
CDP_COMMAND_TIMEOUT = 15.0
DEFAULT_LOGIN_CACHE_TTL_HOURS = 12.0
LOGIN_CACHE_FILE = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "tmp", "login_status_cache.json"))
