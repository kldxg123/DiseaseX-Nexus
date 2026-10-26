"""Tests for the Qwen worker-subprocess client (SPEC section 3.5).

The worker subprocess is fully mocked (``subprocess.Popen`` is monkeypatched);
no real model, torch or transformers is required. These tests pin the
one-JSON-per-line protocol, the ready handshake, the single automatic
restart and the final ``QwenUnavailable`` failure.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from typing import Any

import pytest

from epidemic_core.kg_qwen import qwen_client as qc


def install_fake_popen(monkeypatch: pytest.MonkeyPatch, scripts: list[list[dict[str, Any]]]) -> None:
    """Each Popen spawn pops the next scripted stdout line list."""
    queue = ["".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines) for lines in scripts]
    states: list[dict[str, Any]] = []

    class _Fake:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            self.argv = argv
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(queue.pop(0))
            self._terminated = False
            states.append({"argv": argv, "stdin": self.stdin})

        def poll(self) -> int | None:
            return 0 if self._terminated else None

        def terminate(self) -> None:
            self._terminated = True

        def wait(self, timeout: float | None = None) -> int:
            self._terminated = True
            return 0

        def kill(self) -> None:
            self._terminated = True

    install_fake_popen.states = states  # type: ignore[attr-defined]
    monkeypatch.setattr(qc.subprocess, "Popen", _Fake)


READY = {"ready": True, "model_fingerprint": "f" * 64}
MESSAGES = [{"role": "user", "content": "你好"}]


def make_client() -> qc.QwenClient:
    return qc.QwenClient(model_dir="/fake/model", worker_python="python", timeout_seconds=5)


class TestProtocol:
    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = {
            "request_id": "qwen-req-1",
            "text": '{"plan_version": "v1"}',
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "latency_ms": 123,
        }
        install_fake_popen(monkeypatch, [[READY, response]])
        client = make_client()
        result = client.complete(MESSAGES, max_new_tokens=64)
        assert result.text == '{"plan_version": "v1"}'
        assert result.prompt_tokens == 11
        assert result.completion_tokens == 7
        assert result.latency_ms == 123
        assert result.prompt_sha256 != result.response_sha256
        assert result.response_sha256 == hashlib.sha256(result.text.encode("utf-8")).hexdigest()
        assert client.model_fingerprint == "f" * 64
        # exactly one request line, greedy decoding, on stdin of the first spawn
        stdin_text = install_fake_popen.states[0]["stdin"].getvalue()  # type: ignore[attr-defined]
        request = json.loads(stdin_text.strip())
        assert request["messages"] == MESSAGES
        assert request["max_new_tokens"] == 64
        assert request["do_sample"] is False
        client.close()

    def test_lazy_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake_popen(monkeypatch, [[READY, {"request_id": "qwen-req-1", "text": "ok", "latency_ms": 1}]])
        client = make_client()
        client.complete(MESSAGES)  # worker spawns only on first use
        argv = install_fake_popen.states[0]["argv"]  # type: ignore[attr-defined]
        assert argv[0] == "python"
        assert argv[1].endswith("qwen_worker.py")
        assert argv[2] == "/fake/model"
        client.close()


class TestRestartPolicy:
    def test_worker_error_triggers_single_restart(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scripts = [
            [READY, {"request_id": "qwen-req-1", "error": "CUDA out of memory"}],
            [READY, {"request_id": "qwen-req-2", "text": "recovered", "latency_ms": 5}],
        ]
        install_fake_popen(monkeypatch, scripts)
        client = make_client()
        result = client.complete(MESSAGES)
        assert result.text == "recovered"
        assert len(install_fake_popen.states) == 2  # type: ignore[attr-defined]
        client.close()

    def test_double_failure_raises_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scripts = [
            [READY, {"request_id": "qwen-req-1", "error": "boom"}],
            [READY, {"request_id": "qwen-req-2", "error": "boom again"}],
        ]
        install_fake_popen(monkeypatch, scripts)
        client = make_client()
        with pytest.raises(qc.QwenUnavailable):
            client.complete(MESSAGES)
        assert len(install_fake_popen.states) == 2  # type: ignore[attr-defined]
        client.close()

    def test_never_ready_raises_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake_popen(monkeypatch, [[], []])  # worker exits immediately, twice
        client = make_client()
        with pytest.raises(qc.QwenUnavailable):
            client.complete(MESSAGES)
        client.close()

    def test_complete_after_close_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake_popen(monkeypatch, [[READY]])
        client = make_client()
        client.close()
        with pytest.raises(qc.QwenUnavailable):
            client.complete(MESSAGES)


class TestModelFingerprint:
    def test_deterministic_and_content_sensitive(self) -> None:
        # The system temp dir is not writable in this environment, so the
        # fixture model lives in a workspace-local temporary directory.
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as model_dir:
            with open(os.path.join(model_dir, "config.json"), "w") as fh:
                fh.write('{"a": 1}')
            with open(os.path.join(model_dir, "tokenizer_config.json"), "w") as fh:
                fh.write('{"b": 2}')
            with open(os.path.join(model_dir, "model.safetensors"), "wb") as fh:
                fh.write(b"x" * 2048)
            first = qc.model_fingerprint(model_dir)
            assert first == qc.model_fingerprint(model_dir)
            with open(os.path.join(model_dir, "config.json"), "w") as fh:
                fh.write('{"a": 2}')
            assert qc.model_fingerprint(model_dir) != first

    def test_missing_directory_raises(self) -> None:
        with pytest.raises(qc.QwenUnavailable):
            qc.model_fingerprint("/nonexistent/path/to/model")


class TestPromptTemplate:
    def test_template_sha256_matches_constants(self) -> None:
        expected = hashlib.sha256((qc.SYSTEM_PROMPT + "\n" + qc.USER_PROMPT_TEMPLATE).encode("utf-8")).hexdigest()
        assert expected == qc.PROMPT_TEMPLATE_SHA256

    def test_build_messages_structure(self) -> None:
        context = {"track_key": "t", "round_index": 1}
        messages = qc.build_messages(context, round_index=2, prior_errors=["PLAN_BUDGET_VIOLATION: x"])
        assert [m["role"] for m in messages] == ["system", "user"]
        assert "疫情预测模型调优方案规划器" in messages[0]["content"]
        assert '"track_key": "t"' in messages[1]["content"]
        assert "第 2 轮" in messages[1]["content"]
        assert "PLAN_BUDGET_VIOLATION" in messages[1]["content"]

    def test_build_messages_without_prior_errors(self) -> None:
        messages = qc.build_messages({}, round_index=1)
        assert "校验错误" not in messages[1]["content"]
