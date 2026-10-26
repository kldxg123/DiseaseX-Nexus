#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/projects/openclaw"
out="experiments/sci_q1_ewnet_optimization_20260822"
repo="$out/Epicasting"

if [[ -e "$repo" ]]; then
  echo "REPOSITORY_ALREADY_EXISTS"
  exit 3
fi

mkdir -p "$out"
git clone --filter=blob:none --no-checkout \
  https://github.com/mad-stat/Epicasting.git \
  "$repo"
git -C "$repo" checkout --detach \
  3740776f65c6f4157e67e6a78f71daeade45246b
git -C "$repo" rev-parse HEAD
