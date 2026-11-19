#!/usr/bin/env bash
# CPU-safe by default; use the CUDA image only with confirmed NVIDIA support.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

mode="auto"
ai_profile=false
base_compose="compose.yaml"
for arg in "$@"; do
  case "$arg" in
    auto|cpu|nvidia) mode="$arg" ;;
    --ai) ai_profile=true ;;
    --tailscale) base_compose="compose.tailscale.yaml" ;;
    *) printf 'Unknown option: %s\nUsage: %s [auto|cpu|nvidia] [--ai] [--tailscale]\n' "$arg" "$0" >&2; exit 2 ;;
  esac
done

gpu_available=false
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then
    gpu_available=true
  else
    printf 'NVIDIA GPU detected but NVIDIA Container Toolkit is not configured; CPU is available.\n' >&2
  fi
fi

compose=(docker compose -f "$base_compose")
if [[ "$mode" == nvidia && "$gpu_available" != true ]]; then
  printf 'NVIDIA GPU mode requires a working nvidia-smi and Docker NVIDIA runtime.\n' >&2
  exit 1
fi
if [[ "$mode" == nvidia || ( "$mode" == auto && "$gpu_available" == true ) ]]; then
  compose+=(-f compose.gpu.yaml)
  printf 'ClipForge: NVIDIA CUDA Whisper and NVENC enabled where supported.\n'
else
  printf 'ClipForge: CPU mode (compatible with hosts without a GPU).\n'
fi
if [[ "$ai_profile" == true ]]; then
  compose+=(--profile ai)
  printf 'Ollama sidecar enabled; will receive GPU access in NVIDIA mode.\n'
fi

"${compose[@]}" up -d --build
