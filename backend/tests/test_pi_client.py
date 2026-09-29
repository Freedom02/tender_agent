from __future__ import annotations

import pytest

from backend.app.pi_agent.client import PiError, _extract_text_from_message, parse_json_text
from backend.app.pi_agent.client import PiClient
from backend.app.pi_agent.config import PiConfig


def test_extract_text_only_accepts_assistant_messages() -> None:
    assert _extract_text_from_message(
        {"role": "user", "content": [{"type": "text", "text": '{"from":"user"}'}]}
    ) == ""
    assert _extract_text_from_message(
        {"role": "assistant", "content": [{"type": "text", "text": '{"ok":true}'}]}
    ) == '{"ok":true}'


def test_parse_json_text_accepts_surrounding_prose() -> None:
    assert parse_json_text("结果如下：\n{\"ok\": true}\n以上。") == {"ok": True}


def test_parse_json_text_reports_short_preview_for_invalid_output() -> None:
    with pytest.raises(PiError, match="不是合法 JSON"):
        parse_json_text("模型没有按约定返回结构化结果")


def test_parse_json_text_rejects_truncated_outer_json() -> None:
    with pytest.raises(PiError, match="不是合法 JSON"):
        parse_json_text('{"requirements":[{"id":"req-001","requirement":"测试要求"}')


def test_windows_command_shim_is_launched_through_shell_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PiConfig(
        enabled=True,
        command=r"C:\Users\乔远\AppData\Roaming\npm\pi.cmd",
        provider="company-ai",
        model="gpt-5.5",
        api_key="",
        timeout_seconds=10,
        working_dir="",
    )
    client = PiClient(config)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    command = client._command()

    assert command[:4] == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/c",
        r"C:\Users\乔远\AppData\Roaming\npm\pi.cmd",
    ]
    assert command[4:8] == ["--mode", "rpc", "--no-session", "--no-tools"]
