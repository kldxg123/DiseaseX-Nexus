#!/usr/bin/env bash
set -euo pipefail
PROJECT='USER_HOME/autodl-tmp/epidemic-openclaw'
ENV='USER_HOME/autodl-tmp/conda/envs/qwen-vllm'
MODEL='USER_HOME/autodl-tmp/epidemic-openclaw/models/llm/Qwen3-8B-AWQ'
RUNTIME='USER_HOME/autodl-tmp/epidemic-openclaw/storage/runtime/qwen-vllm'
LOGS='USER_HOME/autodl-tmp/epidemic-openclaw/storage/logs/qwen-vllm'
PIDFILE='USER_HOME/autodl-tmp/epidemic-openclaw/storage/runtime/qwen-vllm/vllm.pid'
HOST='127.0.0.1'
PORT='8000'
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export PATH="$ENV/bin:$PATH"
export CPATH="USER_HOME/autodl-tmp/toolchains/cpython-3.12.13/include/python3.12${CPATH:+:$CPATH}"
export HF_HOME='USER_HOME/autodl-tmp/epidemic-openclaw/storage/cache/huggingface' MODELSCOPE_CACHE='USER_HOME/autodl-tmp/epidemic-openclaw/storage/cache/modelscope' PIP_CACHE_DIR='USER_HOME/autodl-tmp/epidemic-openclaw/storage/cache/pip' TMPDIR='USER_HOME/autodl-tmp/epidemic-openclaw/storage/tmp' XDG_CACHE_HOME='USER_HOME/autodl-tmp/epidemic-openclaw/storage/cache/xdg' TORCH_HOME='USER_HOME/autodl-tmp/epidemic-openclaw/storage/cache/torch' CUDA_VISIBLE_DEVICES=3
export VLLM_USE_FLASHINFER_SAMPLER=0
