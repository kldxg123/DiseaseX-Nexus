#!/usr/bin/env bash
set -euo pipefail
export PATH="USER_HOME/miniconda3/envs/bio_env_py310/bin:$PATH"
exec USER_HOME/miniconda3/envs/bio_env_py310/bin/Rscript /tmp/install_epicasting_r_deps.R
