import json
from agent import orchestrator as orch
from agent import tools


def test_tools_dispatch_word_count():
    assert tools.dispatch("word_count", {"text": "hello\nworld"})["content_len"] == 10


def test_tools_unknown():
    assert "error" in tools.dispatch("nope", {})


def test_run_agent_executes_tool_then_finishes():
    seen = {"tool_result": None}

    def fake_chat_raw(messages, tools=None, **kw):
        # 第一次：要求调用 word_count；第二次：给最终答案
        if not any(m.get("role") == "tool" for m in messages):
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "type": "function",
                 "function": {"name": "word_count", "arguments": json.dumps({"text": "abc"})}}]}
        seen["tool_result"] = [m for m in messages if m.get("role") == "tool"][-1]["content"]
        return {"role": "assistant", "content": "FINAL"}

    out = orch.run_agent("write", "", chat_raw_fn=fake_chat_raw, max_iters=5)
    assert out == "FINAL"
    assert json.loads(seen["tool_result"])["content_len"] == 3
