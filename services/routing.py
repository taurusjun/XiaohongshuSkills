"""渠道规则（机械先判 xhs/gzh；边界由 LLM 兜底）。

来自 SKILL gzh 路由判据：男团/男偶像的『产业·厂牌·销量·战略·行业分析』类 → gzh；
粉丝向爆料/日常/综艺花絮 → xhs。
"""
import re
__all__ = ["route"]

_GZH = re.compile(r"厂牌|销量|首周|万张|oricon|公告牌|榜单|战略|行业|产业|移籍|合同|事务所|公司|制作人|票房|票房冠军|出道|纪念|周年|market|industry", re.I)
_XHS_HINT = re.compile(r"私服|同框|花絮|争议|抽签|综艺|生放送|直播|写真|日常|ins|instagram|街拍", re.I)

def route(title: str = "", body: str = "") -> str:
    text = f"{title} {body[:500]}"
    if _XHS_HINT.search(text) and not _GZH.search(text):
        return "xhs"
    if _GZH.search(text):
        return "gzh"
    return "xhs"
