from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.remote.p0b_utils import LOCAL_REPORTS, remote_project_prelude
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


REMOTE_SEARCH = r"""
python3 - <<'PY'
import json
import os
import time
from pathlib import Path

REPORT_DIR = Path("data/outputs/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
REJECT_TOKENS = ("vl", "omni", "gguf", "awq", "gptq", "1m", "4m")
TOKENIZER_NAMES = ("tokenizer_config.json", "tokenizer.json", "tokenizer.model", "vocab.json", "merges.txt", "special_tokens_map.json")
PRUNE_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    "miniconda3",
    "anaconda3",
    ".conda",
    "envs",
    "site-packages",
    "wandb",
    "mlruns",
    "lora_weights",
}

home = Path.home().resolve()


def sanitize_path(path):
    try:
        resolved = Path(path).resolve()
    except Exception:
        resolved = Path(path)
    text = resolved.as_posix()
    home_text = home.as_posix()
    if text == home_text:
        return "$HOME"
    if text.startswith(home_text + "/"):
        return "$HOME/" + text[len(home_text) + 1 :]
    return "<outside-home>/" + resolved.name


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def safetensors_files(path):
    direct = sorted(path.glob("*.safetensors"))
    index = path / "model.safetensors.index.json"
    if direct:
        return direct, index.exists()
    if not index.exists():
        return [], False
    payload = read_json(index)
    shards = sorted({path / rel for rel in (payload.get("weight_map") or {}).values()})
    return [item for item in shards if item.exists() and item.suffix == ".safetensors"], True


def tokenizer_files(path):
    found = [name for name in TOKENIZER_NAMES if (path / name).exists()]
    has_config = "tokenizer_config.json" in found or "tokenizer.json" in found
    has_vocab = any(name in found for name in ("tokenizer.json", "tokenizer.model", "vocab.json"))
    return has_config and has_vocab, found


def validate_candidate(path, source):
    path = Path(path)
    result = {
        "path": sanitize_path(path),
        "source": source,
        "valid": False,
        "selected": False,
        "blocked_reason": "",
        "warnings": [],
    }
    if not path.exists() or not path.is_dir():
        result["blocked_reason"] = "candidate_dir_missing"
        return result
    config_path = path / "config.json"
    if not config_path.exists():
        result["blocked_reason"] = "missing_config_json"
        return result
    config = read_json(config_path)
    text = path.as_posix().lower() + " " + json.dumps(config, ensure_ascii=False).lower()
    for token in REJECT_TOKENS:
        if token in text:
            result["blocked_reason"] = f"incompatible_variant:{token}"
            return result
    model_type = str(config.get("model_type", "")).lower()
    arch = [str(item) for item in config.get("architectures", [])]
    if "qwen2" not in model_type:
        result["blocked_reason"] = "model_type_not_qwen2"
        return result
    if arch and not any("Qwen2ForCausalLM" in item for item in arch):
        result["blocked_reason"] = "architecture_not_qwen2_causallm"
        return result
    missing = [field for field in ("hidden_size", "num_hidden_layers", "num_attention_heads") if field not in config]
    if missing:
        result["blocked_reason"] = "missing_config_fields:" + ",".join(missing)
        return result
    tok_ok, tok_files = tokenizer_files(path)
    result["tokenizer_files"] = tok_files
    if not tok_ok:
        result["blocked_reason"] = "missing_tokenizer_files"
        return result
    tensors, has_index = safetensors_files(path)
    result["safetensors_count"] = len(tensors)
    result["has_safetensors_index"] = has_index
    result["model_size_gb"] = round(sum(item.stat().st_size for item in tensors) / (1024**3), 2)
    if not tensors:
        result["blocked_reason"] = "missing_safetensors"
        return result
    tokenizer_config = read_json(path / "tokenizer_config.json") if (path / "tokenizer_config.json").exists() else {}
    name_text = path.as_posix().lower()
    has_chat_template = bool(tokenizer_config.get("chat_template"))
    looks_instruct = "instruct" in name_text or has_chat_template
    if "qwen2.5-7b" not in name_text and "qwen25-7b" not in name_text:
        result["blocked_reason"] = "not_qwen25_7b"
        return result
    if not looks_instruct:
        result["blocked_reason"] = "appears_base_not_instruct"
        result["requires_user_confirmation"] = True
        return result
    result.update(
        {
            "valid": True,
            "blocked_reason": "none",
            "model_id": MODEL_ID,
            "is_instruct": True,
            "chat_template_present": has_chat_template,
            "hidden_size": config.get("hidden_size"),
            "num_hidden_layers": config.get("num_hidden_layers"),
            "num_attention_heads": config.get("num_attention_heads"),
        }
    )
    return result


def roots():
    out = []
    env_path = os.environ.get("REMOTE_QWEN25_PATH")
    if env_path:
        out.append((Path(env_path).expanduser(), "REMOTE_QWEN25_PATH"))
    for path in [
        home / "aibola" / "models",
        home / "models",
        home / ".cache" / "huggingface" / "hub",
        home / ".cache" / "modelscope",
        home / ".cache",
        home,
    ]:
        out.append((path, "remote_home_search"))
    seen = set()
    unique = []
    for path, source in out:
        try:
            key = path.resolve().as_posix()
        except Exception:
            continue
        if path.exists() and key not in seen:
            unique.append((path, source))
            seen.add(key)
    return unique


def candidate_like(path):
    lowered = path.as_posix().lower()
    return "qwen" in lowered or (path / "config.json").exists()


def search(max_depth=9, timeout_seconds=240, max_candidates=120):
    started = time.monotonic()
    timed_out = False
    found = []
    seen = set()
    for root, source in roots():
        stack = [(root, 0, source)]
        while stack:
            if time.monotonic() - started > timeout_seconds:
                timed_out = True
                return found, timed_out
            path, depth, src = stack.pop()
            if depth > max_depth or path.name in PRUNE_NAMES:
                continue
            try:
                if candidate_like(path) and (path / "config.json").exists():
                    key = path.resolve().as_posix()
                    if key not in seen:
                        found.append((path, src))
                        seen.add(key)
                        if len(found) >= max_candidates:
                            return found, timed_out
                with os.scandir(path) as entries:
                    children = [Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)]
                for child in children:
                    lowered = child.as_posix().lower()
                    if "qwen" in lowered or depth < max_depth:
                        stack.append((child, depth + 1, src))
            except (OSError, PermissionError):
                continue
    return found, timed_out


env_path = os.environ.get("REMOTE_QWEN25_PATH")
env_result = None
if env_path:
    env_result = validate_candidate(Path(env_path).expanduser(), "REMOTE_QWEN25_PATH")
    if env_result.get("valid"):
        env_result["selected"] = True
        result = {
            "status": "ok",
            "blocked_reason": "none",
            "selected": env_result,
            "valid_candidates": [env_result],
            "invalid_candidates": [],
            "multiple_candidates_need_confirmation": False,
            "search_timed_out": False,
            "remote_qwen25_path_was_set": True,
        }
    else:
        result = None
else:
    result = None

if result is None:
    raw, timed_out = search()
    valid = []
    invalid = []
    for path, source in raw:
        item = validate_candidate(path, source)
        if item.get("valid"):
            valid.append(item)
        else:
            invalid.append(item)
    if env_result and not env_result.get("valid"):
        invalid.insert(0, env_result)
    if len(valid) == 1:
        valid[0]["selected"] = True
        status = "ok"
        blocked_reason = "none"
    elif len(valid) > 1:
        status = "blocked"
        blocked_reason = "multiple_valid_remote_qwen_candidates_need_user_confirmation"
    else:
        status = "blocked"
        blocked_reason = "remote_qwen_search_timed_out" if timed_out else "remote_qwen_model_not_found"
    result = {
        "status": status,
        "blocked_reason": blocked_reason,
        "selected": valid[0] if len(valid) == 1 else None,
        "valid_candidates": valid,
        "invalid_candidates": invalid[:50],
        "multiple_candidates_need_confirmation": len(valid) > 1,
        "search_timed_out": timed_out,
        "remote_qwen25_path_was_set": bool(env_path),
        "searched_roots": [sanitize_path(path) for path, _ in roots()],
    }

(REPORT_DIR / "remote_qwen_model_candidates.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
selected = result.get("selected") or {}
lines = [
    "# Remote Qwen Model Search Report",
    "",
    f"status: {result['status']}",
    f"blocked_reason: {result['blocked_reason']}",
    f"remote_qwen25_path_was_set: {str(result.get('remote_qwen25_path_was_set', False)).lower()}",
    f"search_timed_out: {str(result.get('search_timed_out', False)).lower()}",
    f"multiple_candidates_need_confirmation: {str(result.get('multiple_candidates_need_confirmation', False)).lower()}",
    f"selected_model_path: {selected.get('path', 'none')}",
    f"selected_is_instruct: {str(selected.get('is_instruct', False)).lower()}",
    f"selected_model_size_gb: {selected.get('model_size_gb', 'not_available')}",
    f"valid_candidate_count: {len(result.get('valid_candidates', []))}",
    f"invalid_candidate_count: {len(result.get('invalid_candidates', []))}",
    "",
    "## Valid Candidates",
]
for item in result.get("valid_candidates", []):
    lines.append(f"- {item.get('path')} source={item.get('source')} size_gb={item.get('model_size_gb')} chat_template={item.get('chat_template_present')}")
(REPORT_DIR / "remote_qwen_model_search_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({k: result[k] for k in ["status", "blocked_reason", "multiple_candidates_need_confirmation", "search_timed_out"]}, ensure_ascii=False))
raise SystemExit(0 if result["status"] == "ok" else (3 if result.get("multiple_candidates_need_confirmation") else 2))
PY
"""


def _pull(runner: SSHRunner, remote_name: str) -> int:
    local = LOCAL_REPORTS / remote_name
    result = runner.sftp_get_file(f"{runner.config.remote_dir}/data/outputs/reports/{remote_name}", local)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Search remote $HOME for a staged Qwen2.5-7B-Instruct model without downloading.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=360, dry_run=args.dry_run)
    command = remote_project_prelude(cfg, create=True) + REMOTE_SEARCH
    result = runner.run(command, "logs/remote/find_remote_qwen_sanitized.log")
    print(result.stdout or result.stderr)
    if result.returncode in {0, 2, 3}:
        _pull(runner, "remote_qwen_model_search_report.md")
        _pull(runner, "remote_qwen_model_candidates.json")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
