#!/usr/bin/env bash
# twinpc/setup-ai-env.sh — PyTorch on the twin's RX 6600 (ROCm) in ~/ai-env. Run ON THE TWIN as the
# desktop user (no sudo); `twin run` jobs put ~/ai-env/bin first on PATH. ~4 GB download.
set -euo pipefail
export PATH=$HOME/.local/share/mise/shims:$PATH UV_HTTP_TIMEOUT=900
command -v uv >/dev/null || mise use -g uv@latest
[[ -d ~/ai-env ]] || uv venv --python 3.12 ~/ai-env
VIRTUAL_ENV=~/ai-env uv pip install --index-url https://download.pytorch.org/whl/rocm6.4 torch torchvision torchaudio
HSA_OVERRIDE_GFX_VERSION=10.3.0 ~/ai-env/bin/python -c 'import torch; print("GPU:", torch.cuda.is_available(), torch.cuda.get_device_name(0))'
