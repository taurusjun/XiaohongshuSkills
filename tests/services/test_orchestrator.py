from agent import orchestrator as orch


def test_build_messages_includes_skill_and_instruction():
    msgs = orch.build_messages("daily-material-review", "CTX")
    assert msgs[0]["role"] == "system"
    assert "SKILL" in msgs[0]["content"] and len(msgs[0]["content"]) > 1000
    assert "每日素材 review" in msgs[1]["content"]
    assert "CTX" in msgs[1]["content"]


def test_run_task_uses_injected_llm():
    out = orch.run_task("write", "", chat_fn=lambda msgs: "DONE")
    assert out == "DONE"


def test_unknown_task():
    import pytest
    with pytest.raises(ValueError):
        orch.build_messages("nope", "")
