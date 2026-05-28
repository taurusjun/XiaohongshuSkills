#!/usr/bin/env python3
"""xhs-llm MCP server — LiteLLM 语义化封装（翻译/评分/生成/图片评分/override分析）"""

import sys, os, json, base64, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP
from scripts.yahoo_common import call_litellm, build_scoring_prompt
from scripts.sqlite_db import load_active_dimensions, get_score_dims
from config.yahoo_conf import GALLERY_CACHE_DIR, VISION_ENABLED

mcp = FastMCP("xhs-llm")


def _error(code: str, msg: str) -> dict:
    return {"error": True, "code": code, "message": msg}


@mcp.tool()
def translate_and_classify(title_ja: str, content_ja: str) -> dict:
    """翻译日文标题+摘要，判断内容适合的体裁类型（单选）"""
    system = (
        "You are a Japanese-to-Chinese translator and content classifier. "
        "Output ONLY valid JSON, no extra text."
    )
    prompt = f"""翻译以下日文内容，并判断最适合的小红书内容形式（必须选唯一1个）。

日文标题：{title_ja}
日文正文（前800字）：{content_ja[:800]}

体裁说明（从4个中选1个最合适的）：
- news: 资讯体，任何内容都适用
- story: 故事体，内容有时间弧度或前后变化
- ranking: 盘点体，可提炼≥3个并列元素
- comparison: 对比体，含两个以上可比较对象

返回严格 JSON：
{{
  "title_zh": "中文标题",
  "summary_zh": "2-3句中文摘要（共50-80字）",
  "format": "news",
  "reason": "一句话说明为什么适合这个形式"
}}"""
    try:
        result = call_litellm(
            prompt, system_prompt=system,
            temperature=0.2, max_tokens=500,
            response_format={"type": "json_object"},
        )
        if not result:
            return _error("LLM_UNAVAILABLE", "LLM call returned empty")
        data = json.loads(result)
        # Normalize: accept old "format_suitability" array or new "format" string
        fs = data.get("format", data.get("format_suitability", "news"))
        if isinstance(fs, list):
            fs = fs[0] if fs else "news"
        data["format"] = fs
        return data
    except json.JSONDecodeError as e:
        return _error("PARSE_ERROR", str(e))
    except Exception as e:
        return _error("LLM_ERROR", str(e))


@mcp.tool()
def evaluate_content(title: str, content_ja: str, comment: str = "", dim_version: str = "") -> dict:
    """评估内容质量，返回所有评分维度的 value 和 reason"""
    try:
        if dim_version:
            from scripts.sqlite_db import _connect
            with _connect() as db:
                row = db.execute(
                    "SELECT dimensions_json FROM scoring_dimension_versions WHERE version=?",
                    (dim_version,),
                ).fetchone()
            if row:
                dims_cfg = json.loads(row["dimensions_json"])
            else:
                return _error("NOT_FOUND", f"Version {dim_version} not found")
        else:
            dims_cfg = load_active_dimensions()

        if not dims_cfg:
            return _error("LLM_ERROR", "No active dimension definitions")

        dim_prompt = build_scoring_prompt(dims_cfg)
        all_dims = [d["name"] for d in dims_cfg]

        prompt = f"""评估笔记内容质量，{len(all_dims)} 个维度各判 0、0.5 或 1，每维度附一句理由。

{dim_prompt}

标题：{title}
正文：{content_ja[:800]}
解读：{comment[:150]}

返回严格 JSON：
{{"维度名": {{"value": 0或0.5或1, "reason": "15-50字理由"}}, ...}}
所有 {len(all_dims)} 个维度都必须出现，value 只能是 0、0.5、1 三个值之一。"""

        result = call_litellm(
            prompt,
            system_prompt="You are a JSON API. Output ONLY valid JSON.",
            temperature=0.1, max_tokens=4000,
            response_format={"type": "json_object"},
        )
        if not result:
            return _error("LLM_UNAVAILABLE", "LLM call returned empty")

        data = json.loads(result)
        data["_dim_version"] = dim_version or "active"
        return data
    except json.JSONDecodeError as e:
        return _error("PARSE_ERROR", str(e))
    except Exception as e:
        return _error("LLM_ERROR", str(e))


@mcp.tool()
def generate_content(title_ja: str, body_text: str, format: str = "news",
                     style: str = "normal") -> dict:
    """生成小红书内容（SEO标题+正文+评论+话题标签）"""
    style_notes = {
        "normal": "自然亲切的口语化风格",
        "tsundere": "傲娇风格：用略带吐槽和傲娇的语气，表面嫌弃实则关心，善用「哼」「真是的」「勉为其难告诉你」等语气词",
    }
    style_prompt = style_notes.get(style, style_notes["normal"])
    system = f"""你是小红书日本娱乐博主，{style_prompt}。
输出 ONLY valid JSON，不要任何额外文字。"""

    prompt = f"""基于以下日文资讯，生成一篇小红书笔记。

日文标题：{title_ja}
日文正文：{body_text[:1500]}
体裁：{format}

return strict JSON:
{{
  "seo_title": "中文SEO标题（15-25字，含关键词和悬念）",
  "summary": "2-3句摘要（40-60字）",
  "content": "正文（200-400字，分段，emoji点缀，口语化）",
  "comment": "文末互动引导（10-20字，开放式提问）",
  "tags": {{
    "precise": ["精准标签1", "精准标签2"],
    "vertical": ["垂类标签1", "垂类标签2"],
    "broad": ["泛流量标签1"]
  }}
}}"""
    try:
        result = call_litellm(
            prompt, system_prompt=system,
            temperature=0.7, max_tokens=6000,
            response_format={"type": "json_object"},
        )
        if not result:
            return _error("LLM_UNAVAILABLE", "LLM call returned empty")
        data = json.loads(result)
        # 确保 tags 结构正确
        if "tags" in data and isinstance(data["tags"], dict):
            for key in ("precise", "vertical", "broad"):
                if key not in data["tags"]:
                    data["tags"][key] = []
                if isinstance(data["tags"][key], str):
                    data["tags"][key] = [data["tags"][key]]
        return data
    except json.JSONDecodeError as e:
        return _error("PARSE_ERROR", str(e))
    except Exception as e:
        return _error("LLM_ERROR", str(e))


@mcp.tool()
def generate_video_caption(video_context: str, style: str = "normal") -> dict:
    """为视频生成配文（80-120字）"""
    style_notes = {"normal": "轻松口语化", "tsundere": "傲娇语气"}
    prompt = f"""你是小红书博主。为以下视频内容撰写配文。
视频内容：{video_context[:300]}
风格：{style_notes.get(style, '轻松口语化')}

返回 JSON：
{{"caption": "视频配文，80-120字，每句换行，第一句悬念≤15字，最后互动召唤≤10字"}}"""
    try:
        result = call_litellm(
            prompt,
            system_prompt="只输出 JSON，不要任何分析文字。",
            temperature=0.5, max_tokens=800,
            response_format={"type": "json_object"},
        )
        if not result:
            return _error("LLM_UNAVAILABLE", "LLM call returned empty")
        return json.loads(result)
    except json.JSONDecodeError as e:
        return _error("PARSE_ERROR", str(e))
    except Exception as e:
        return _error("LLM_ERROR", str(e))


@mcp.tool()
def analyze_overrides(dim_name: str, override_notes: list[str]) -> dict:
    """分析某维度的人工纠正记录，找规律"""
    if len(override_notes) < 3:
        return {"has_pattern": False, "reason": "样本不足，需要至少 3 条纠正记录"}

    notes_text = "\n".join(f"{i+1}. {n}" for i, n in enumerate(override_notes))
    prompt = f"""分析以下对「{dim_name}」维度的人工评分纠正记录，判断是否存在规律性模式。

纠正记录：
{notes_text}

返回 JSON：
{{
  "has_pattern": true或false,
  "edge_case": "如有规律，描述新增的边界说明（一句话），无则为空",
  "evidence": "支持结论的证据摘要（1-2句）",
  "reason": "如无规律，说明原因"
}}"""
    try:
        result = call_litellm(
            prompt,
            system_prompt="You are a data analyst. Output ONLY valid JSON.",
            temperature=0.3, max_tokens=1000,
            response_format={"type": "json_object"},
        )
        if not result:
            return _error("LLM_UNAVAILABLE", "LLM call returned empty")
        return json.loads(result)
    except json.JSONDecodeError as e:
        return _error("PARSE_ERROR", str(e))
    except Exception as e:
        return _error("LLM_ERROR", str(e))


@mcp.tool()
def score_cover_image(image_path: str) -> dict:
    """对封面图进行 6 个客观维度评分（视觉模型，由 VISION_ENABLED 开关控制）

    维度：清晰度/构图/情绪吸引力/色彩表现力/信息传达/品牌一致性
    每维度 value: 0/0.5/1
    """
    if not VISION_ENABLED:
        return _error("NOT_IMPLEMENTED",
                      "封面图评分未启用。设置 VISION_ENABLED=1 并配置 VISION_MODEL 后重试。")

    abs_path = os.path.abspath(os.path.expanduser(image_path))
    cache_dir = os.path.abspath(os.path.expanduser(GALLERY_CACHE_DIR))
    if not abs_path.startswith(cache_dir):
        return _error("INVALID_VALUE", f"Image path must be under {GALLERY_CACHE_DIR}")

    if not os.path.isfile(abs_path):
        return _error("NOT_FOUND", f"Image not found: {abs_path}")

    try:
        from PIL import Image
        img = Image.open(abs_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > 512:
            ratio = 512.0 / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        return _error("IMAGE_ERROR", f"Failed to process image: {e}")

    prompt = """评估这张图片作为小红书封面图的质量，6 个维度各判 0、0.5 或 1。

【清晰度】图片是否清晰，无明显模糊或马赛克
  ✅ 1：主体清晰，细节可见，文字可读
  ❌ 0：整体模糊，或主体区域有马赛克/噪点

【构图】视觉重点是否突出，构图是否专业
  ✅ 1：主体突出，有明确视觉中心，留白合理
  ❌ 0：杂乱无章，主体不明确

【情绪吸引力】是否能引发点击欲望和情感共鸣
  ✅ 1：让人想点击了解更多，有情感冲击力
  ❌ 0：平淡无奇，看完即忘

【色彩表现力】色彩搭配是否美观、有视觉冲击力
  ✅ 1：配色和谐或高对比度，有视觉冲击
  ❌ 0：昏暗/过曝/色彩单调无层次

【信息传达】封面是否有效传达了内容主题
  ✅ 1：一眼能看出内容主题，图文匹配
  ❌ 0：看不出内容是什么，或文不对图

【品牌一致性】是否适合小红书日本娱乐账号调性
  ✅ 1：适合，风格与日娱账号人设一致
  ❌ 0：与账号调性不符（如过于严肃/商业/低质）

返回严格 JSON：
{{
  "清晰度": {{"value": 0或0.5或1, "reason": "15-50字理由"}},
  "构图": {{"value": 0或0.5或1, "reason": "..."}},
  "情绪吸引力": {{"value": 0或0.5或1, "reason": "..."}},
  "色彩表现力": {{"value": 0或0.5或1, "reason": "..."}},
  "信息传达": {{"value": 0或0.5或1, "reason": "..."}},
  "品牌一致性": {{"value": 0或0.5或1, "reason": "..."}}
}}"""

    try:
        import requests
        LITELLM_URL = os.environ.get("LITELLM_URL", "").rstrip("/")
        LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
        LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "")

        if not LITELLM_API_KEY:
            return _error("LLM_UNAVAILABLE", "LITELLM_API_KEY not configured")

        vision_model = os.environ.get("VISION_MODEL", LITELLM_MODEL)

        body = {
            "model": vision_model,
            "messages": [
                {"role": "system", "content": "You are a visual content evaluator. Output ONLY valid JSON."},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": prompt},
                ]},
            ],
            "max_tokens": 1000,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        resp = requests.post(
            f"{LITELLM_URL}/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {LITELLM_API_KEY}"},
            json=body,
            timeout=(30, 300),
        )
        if resp.status_code != 200:
            return _error("LLM_UNAVAILABLE", f"Vision API returned {resp.status_code}")

        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return _error("LLM_ERROR", "Vision API returned empty content")
        return json.loads(content.strip())
    except json.JSONDecodeError as e:
        return _error("PARSE_ERROR", str(e))
    except Exception as e:
        return _error("LLM_ERROR", str(e))


if __name__ == "__main__":
    mcp.run()
