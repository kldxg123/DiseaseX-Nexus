"""Persistent-subprocess client for local Qwen CPU inference (SPEC section 3.5).

Safety/design invariants:

  * this module is pure stdlib: torch/transformers exist only inside the
    worker subprocess, so the client imports cleanly in any environment;
  * generation is greedy (``do_sample=False``) for determinism;
  * the worker is a long-lived child process speaking one-JSON-per-line on
    stdin/stdout; a call times out after ``timeout_seconds`` and a dead or
    failing worker is restarted exactly once before ``QwenUnavailable`` is
    raised — callers fall back to ``deterministic_fallback_plan``;
  * the prompt template is a module constant whose sha256
    (``PROMPT_TEMPLATE_SHA256``) is recorded on every agent run;
  * the system prompt forbids commands, SQL, code, paths, URLs, promotion
    requests, test-label references and PAPER_EXTRACTED provenance claims.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

from epidemic_core.kg_qwen import QWEN_MODEL_DIR, QWEN_WORKER_PYTHON
from epidemic_core.kg_r3c.execution import sha256_json, sha256_text

DEFAULT_TIMEOUT_SECONDS = 1800
# CPU inference budget: keep plans compact so one planning call finishes in
# minutes, not tens of minutes (measured ~0.7-1 tok/s on the vm201 CPU).
# 768 leaves headroom over the observed ~370-token accepted plan while still
# bounding worst-case latency.
DEFAULT_MAX_NEW_TOKENS = 768

SYSTEM_PROMPT = (
    "你是疫情预测模型调优方案规划器。你的唯一职责是根据给定的数据集画像、候选模型与参数合同，"
    "产出下一轮自动调优方案。硬性规则："
    "1）只能输出一个严格合法的 JSON 对象，不得输出任何其他文字、markdown 标记或解释；"
    "2）禁止输出任何命令、SQL、代码、文件路径或 URL；"
    "3）不得请求 promotion、审批（approval）或任何测试标签（test label）信息；"
    "4）所有参数来源不得声称来自论文提取，只能使用 IMPLEMENTATION_DEFAULT、"
    "DATA_PROFILE_RULE 或 OPTIMIZER_SELECTED；"
    "5）方案必须遵守给定的预算上限与参数合同边界，candidate_models 只能来自白名单候选；"
    "6）保持简洁：直接以 { 开头输出紧凑 JSON，rationale_zh 不超过 3 条且每条不超过 20 字，"
    "known_risks、stop_conditions 各不超过 2 条，每个参数的 rationale 不超过 12 字，"
    "candidate_models 不超过 3 个，parameter_spaces 不超过 8 项，总输出不超过 480 个 token。"
)

USER_PROMPT_TEMPLATE = (
    "当前为第 {round_index} 轮调优。以下是白名单上下文（JSON）：\n"
    "{context_json}\n\n"
    "请输出一个紧凑 JSON 对象（直接以 {{ 开头，总输出不超过 480 token），只允许以下顶层字段：\n"
    "plan_version（固定 \"v1\"）、dataset_snapshot_sha256（必须等于上下文中的 snapshot_sha256）、"
    "candidate_models（白名单候选，不超过 3 个）、parameter_spaces（不超过 8 项，每项含 "
    "adapter_id、parameter_name、transform（none 或 log）、parameter_source、rationale（不超过 12 字）、"
    "evidence_ref（代码来源标识符，仅字母数字与 _:.-）；数值空间给 lower 和 upper，离散空间给 values）、"
    "rationale_zh（不超过 3 条，每条不超过 20 字）、known_risks（不超过 2 条）、"
    "stop_conditions（不超过 2 条）。\n"
    "objective、feature_recipes、max_rounds、max_trials_per_round、max_total_minutes、kg_evidence_ids "
    "由平台按固定合同补齐，你不要输出这些字段。{prior_errors_block}"
)

# Content hash of the exact prompt template constants, recorded on every run.
PROMPT_TEMPLATE_SHA256: str = sha256_text(SYSTEM_PROMPT + "\n" + USER_PROMPT_TEMPLATE)


class QwenUnavailable(RuntimeError):
    """Raised when the worker cannot produce a response (after one restart)."""


@dataclass(frozen=True)
class QwenResponse:
    text: str
    prompt_sha256: str
    response_sha256: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


def model_fingerprint(model_dir: str) -> str:
    """Content fingerprint of the local model directory.

    sha256 over sorted entries: for ``config.json``, ``tokenizer_config.json``,
    ``generation_config.json`` and ``model.safetensors.index.json`` (when
    present) the entry is ``(filename, size, sha256 of first 1MB)``; for every
    ``*.safetensors`` shard the entry is ``(filename, size)``.
    """
    hashed_names = (
        "config.json",
        "tokenizer_config.json",
        "generation_config.json",
        "model.safetensors.index.json",
    )
    entries: list[list[Any]] = []
    for name in hashed_names:
        path = os.path.join(model_dir, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(1024 * 1024)
        entries.append([name, size, hashlib.sha256(head).hexdigest()])
    try:
        dir_entries = os.listdir(model_dir)
    except FileNotFoundError as exc:
        raise QwenUnavailable(f"model directory not found: {model_dir}") from exc
    for name in dir_entries:
        if not name.endswith(".safetensors"):
            continue
        path = os.path.join(model_dir, name)
        if os.path.isfile(path):
            entries.append([name, os.path.getsize(path)])
    entries.sort(key=lambda e: str(e[0]))
    return sha256_json(entries)


def build_messages(
    context: dict[str, Any],
    round_index: int,
    prior_errors: list[str] | None = None,
) -> list[dict[str, str]]:
    """Assemble the system+user message pair for one planning call."""
    if prior_errors:
        prior_errors_block = "上一次输出未通过校验，请逐条修正以下错误后重新输出：\n" + "\n".join(
            f"- {error}" for error in prior_errors
        )
    else:
        prior_errors_block = ""
    user = USER_PROMPT_TEMPLATE.format(
        round_index=round_index,
        context_json=json.dumps(context, ensure_ascii=False),
        prior_errors_block=prior_errors_block,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _worker_script_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "qwen_worker.py")


class QwenClient:
    """Lazily-started persistent Qwen worker subprocess (one request in flight).

    ``complete`` writes one JSON line to the worker's stdin and reads one JSON
    line back (matched by ``request_id``). On timeout, protocol error, worker
    error or process death the worker is restarted once; a second failure
    raises ``QwenUnavailable``.
    """

    def __init__(
        self,
        model_dir: str = QWEN_MODEL_DIR,
        worker_python: str = QWEN_WORKER_PYTHON,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.model_dir = model_dir
        self.worker_python = worker_python
        self.timeout_seconds = timeout_seconds
        self.model_fingerprint: str | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[dict[str, Any] | None] | None = None
        self._request_counter = 0
        self._closed = False

    # ------------------------------------------------------------ lifecycle
    def _read_loop(self, proc: subprocess.Popen[str], lines: queue.Queue[dict[str, Any] | None]) -> None:
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    lines.put({"error": f"WORKER_PROTOCOL: non-JSON line: {raw[:200]!r}"})
                    continue
                if isinstance(obj, dict):
                    lines.put(obj)
                else:
                    lines.put({"error": "WORKER_PROTOCOL: non-object line"})
        finally:
            lines.put(None)  # EOF: process exited or closed its stdout

    def _start(self) -> None:
        self._kill()
        proc = subprocess.Popen(
            [self.worker_python, _worker_script_path(), self.model_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # worker logs go to the parent stderr (no pipe deadlock)
            text=True,
            bufsize=1,
        )
        lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
        threading.Thread(target=self._read_loop, args=(proc, lines), daemon=True).start()
        self._proc = proc
        self._lines = lines
        ready = self._next_line()
        if ready is None:
            raise QwenUnavailable("QWEN_UNAVAILABLE: worker exited before ready signal")
        if not ready.get("ready"):
            raise QwenUnavailable(f"QWEN_UNAVAILABLE: worker not ready: {ready.get('error', ready)!r}")
        fingerprint = ready.get("model_fingerprint")
        self.model_fingerprint = fingerprint if isinstance(fingerprint, str) else None

    def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        self._lines = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()

    def _ensure_started(self) -> None:
        if self._closed:
            raise QwenUnavailable("QWEN_UNAVAILABLE: client is closed")
        if self._proc is None or self._proc.poll() is not None:
            self._start()

    def _next_line(self) -> dict[str, Any] | None:
        assert self._lines is not None
        try:
            return self._lines.get(timeout=self.timeout_seconds)
        except queue.Empty:
            raise QwenUnavailable(
                f"QWEN_UNAVAILABLE: worker response timeout after {self.timeout_seconds}s"
            ) from None

    # ------------------------------------------------------------ inference
    def complete(self, messages: list[dict[str, str]], max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS) -> QwenResponse:
        last_error: QwenUnavailable | None = None
        for _attempt in (1, 2):  # one automatic restart, then give up
            try:
                self._ensure_started()
                return self._roundtrip(messages, max_new_tokens)
            except QwenUnavailable as exc:
                last_error = exc
                self._kill()
        assert last_error is not None
        raise last_error

    def _roundtrip(self, messages: list[dict[str, str]], max_new_tokens: int) -> QwenResponse:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise QwenUnavailable("QWEN_UNAVAILABLE: worker not running")
        self._request_counter += 1
        request_id = f"qwen-req-{self._request_counter}"
        payload = {
            "request_id": request_id,
            "messages": messages,
            "max_new_tokens": max_new_tokens,
            "do_sample": False,
        }
        line = json.dumps(payload, ensure_ascii=False)
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise QwenUnavailable(f"QWEN_UNAVAILABLE: worker stdin broken: {exc}") from exc
        response = self._next_line()
        if response is None:
            raise QwenUnavailable("QWEN_UNAVAILABLE: worker exited mid-request")
        if response.get("request_id") not in (request_id, None):
            raise QwenUnavailable(
                f"QWEN_UNAVAILABLE: response request_id {response.get('request_id')!r} != {request_id!r}"
            )
        error = response.get("error")
        if error is not None:
            raise QwenUnavailable(f"QWEN_UNAVAILABLE: worker error: {error}")
        text = response.get("text")
        if not isinstance(text, str):
            raise QwenUnavailable("QWEN_UNAVAILABLE: worker response missing text")
        latency = response.get("latency_ms")
        return QwenResponse(
            text=text,
            prompt_sha256=sha256_text(line),
            response_sha256=sha256_text(text),
            latency_ms=int(latency) if isinstance(latency, (int, float)) else 0,
            prompt_tokens=response.get("prompt_tokens") if isinstance(response.get("prompt_tokens"), int) else None,
            completion_tokens=(
                response.get("completion_tokens") if isinstance(response.get("completion_tokens"), int) else None
            ),
        )

    def close(self) -> None:
        self._closed = True
        self._kill()

    def __enter__(self) -> QwenClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
