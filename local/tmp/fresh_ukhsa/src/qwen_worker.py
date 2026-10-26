"""Standalone Qwen inference worker (SPEC section 3.5).

Executed as an independent subprocess by ``qwen_client.QwenClient`` using the
Ebola venv python (which has torch/transformers). This is the only kg_qwen
module allowed to import torch/transformers, and it does so lazily inside
``main`` so importing this file stays stdlib-only.

Protocol (one JSON object per line):

  * stdin:  ``{"request_id", "messages", "max_new_tokens", "do_sample": false}``
  * stdout: first ``{"ready": true, "model_fingerprint": ...}`` once the model
    is loaded, then one ``{"request_id", "text", "prompt_tokens",
    "completion_tokens", "latency_ms", "finish_reason"}`` per request, or
    ``{"request_id", "error": ...}`` on failure;
  * stderr: human-readable logs only.

Generation is greedy (``do_sample=False``) so identical prompts reproduce
identical text. The model is loaded with ``local_files_only=True``,
``torch_dtype=bfloat16`` and ``device_map="cpu"``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any


def _log(message: str) -> None:
    print(f"[qwen_worker] {message}", file=sys.stderr, flush=True)


def _fingerprint(model_dir: str) -> str:
    """Self-contained fingerprint (same algorithm as qwen_client.model_fingerprint).

    The worker runs under a different interpreter without the platform package
    on sys.path, so the algorithm is duplicated here instead of imported.
    """
    hashed_names = (
        "config.json",
        "tokenizer_config.json",
        "generation_config.json",
        "model.safetensors.index.json",
    )
    entries: list[list[object]] = []
    for name in hashed_names:
        path = os.path.join(model_dir, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(1024 * 1024)
        entries.append([name, size, hashlib.sha256(head).hexdigest()])
    for name in sorted(os.listdir(model_dir)):
        if name.endswith(".safetensors"):
            entries.append([name, os.path.getsize(os.path.join(model_dir, name))])
    entries.sort(key=lambda e: str(e[0]))
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _log("usage: qwen_worker.py <model_dir>")
        return 2
    model_dir = argv[1]

    import torch  # type: ignore[import-not-found]  # noqa: PLC0415 - lazy by design (see module docstring)
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]  # noqa: PLC0415

    _log(f"loading tokenizer from {model_dir}")
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    torch.set_num_threads(os.cpu_count() or 4)  # use all cores for CPU inference
    _log(f"loading model (bfloat16, cpu, threads={torch.get_num_threads()})")
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        device_map="cpu",
    )
    model.eval()

    ready = {"ready": True, "model_fingerprint": _fingerprint(model_dir)}
    print(json.dumps(ready, ensure_ascii=False), flush=True)
    _log("ready")

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        request_id: Any = None
        try:
            request = json.loads(raw)
            request_id = request.get("request_id")
            messages = request["messages"]
            max_new_tokens = int(request.get("max_new_tokens", 2048))
            started = time.perf_counter()
            encoded = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
                return_dict=True,
            )
            if hasattr(encoded, "keys"):  # transformers v5: BatchEncoding
                input_ids = encoded["input_ids"]
                generate_inputs = dict(encoded)
            else:  # legacy: bare LongTensor
                input_ids = encoded
                generate_inputs = {"input_ids": encoded}
            with torch.no_grad():
                output_ids = model.generate(
                    **generate_inputs,
                    do_sample=False,
                    max_new_tokens=max_new_tokens,
                )
            completion_ids = output_ids[0][input_ids.shape[1] :]
            text = tokenizer.decode(completion_ids, skip_special_tokens=True)
            latency_ms = int((time.perf_counter() - started) * 1000)
            finish_reason = "length" if int(completion_ids.shape[0]) >= max_new_tokens else "stop"
            response = {
                "request_id": request_id,
                "text": text,
                "prompt_tokens": int(input_ids.shape[1]),
                "completion_tokens": int(completion_ids.shape[0]),
                "latency_ms": latency_ms,
                "finish_reason": finish_reason,
            }
        except Exception as exc:  # keep the worker alive after a bad request
            response = {"request_id": request_id, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    _log("stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
