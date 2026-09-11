#!/usr/bin/env bash
# Runs Ollama natively on macOS so it can use the Metal GPU.
# Docker on macOS has no GPU access, so Ollama must not run in Compose.
# Every value can be overridden from the shell, e.g. OLLAMA_NUM_PARALLEL=4 ./scripts/ollama-serve.sh
set -euo pipefail

# Bind to loopback only. Ollama has no authentication, so 0.0.0.0 would expose
# it to everyone on your network.
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"

# Keep models loaded between calls to avoid multi-second cold starts.
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"

# Concurrent requests per model. Each slot adds KV-cache memory.
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"

# Agent model + embedding model. The eval judge is loaded in a separate phase.
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-2}"

# Shrink KV-cache memory.
export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"
export OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV_CACHE_TYPE:-q8_0}"

# Server-wide default context. Deskpilot also sets num_ctx per request.
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-32768}"

exec ollama serve
