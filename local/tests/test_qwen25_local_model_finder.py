import json

from src.models.qwen25_local_model_finder import validate_candidate


def _make_candidate(path, name="Qwen2.5-7B-Instruct", chat=True):
    model = path / name
    model.mkdir()
    (model / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen2",
                "architectures": ["Qwen2ForCausalLM"],
                "hidden_size": 3584,
                "num_hidden_layers": 28,
                "num_attention_heads": 28,
            }
        ),
        encoding="utf-8",
    )
    (model / "tokenizer_config.json").write_text(json.dumps({"chat_template": "{{ messages }}" if chat else ""}), encoding="utf-8")
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model / "model-00001-of-00001.safetensors").write_bytes(b"stub")
    return model


def test_valid_instruct_candidate(tmp_path):
    model = _make_candidate(tmp_path)
    result = validate_candidate(model)
    assert result["valid"] is True
    assert result["blocked_reason"] == "none"


def test_reject_base_candidate_without_chat(tmp_path):
    model = _make_candidate(tmp_path, name="Qwen2.5-7B", chat=False)
    result = validate_candidate(model)
    assert result["valid"] is False
    assert result["blocked_reason"] == "appears_base_not_instruct"


def test_reject_incompatible_variant(tmp_path):
    model = _make_candidate(tmp_path, name="Qwen2.5-VL-7B-Instruct", chat=True)
    result = validate_candidate(model)
    assert result["valid"] is False
    assert result["blocked_reason"].startswith("incompatible_variant")
