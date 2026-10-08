"""CDP 异常类型 —— 从 cdp_publish.py 抽出（P6 拆分）。"""


class _PromiseCollectedError(Exception):
    """Internal: Chrome GC collected a CDP Promise. Caller should retry."""


class CDPError(Exception):
    """Error communicating with Chrome via CDP."""


class XHSRateLimitError(CDPError):
    """小红书触发频率限制（安全验证弹窗），需等待后重试。"""


