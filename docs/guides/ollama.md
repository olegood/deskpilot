# Ollama guide

How Deskpilot runs local models, and how to tune and troubleshoot them.

> Last verified against: milestone 2 (complete).

## Why Ollama runs outside Docker

Docker on macOS runs containers inside a Linux VM that has no access to the Apple GPU. Ollama in a container would run on the CPU, many times slower. So Ollama runs natively, started by a script in the repo. See [decision D-003](../decisions.md#d-003-ollama-runs-natively-on-the-host-not-in-docker).

## Starting Ollama

```bash
./scripts/ollama-serve.sh
```

Don't use the Ollama desktop app or `brew services` at the same time. They would compete for port 11434 and ignore the script's settings.

Any setting can be overridden for one run:

```bash
OLLAMA_NUM_PARALLEL=4 ./scripts/ollama-serve.sh
```

## Script settings

| Variable | Value | Why |
|---|---|---|
| `OLLAMA_HOST` | `127.0.0.1:11434` | Loopback only. Ollama has no authentication, so binding to `0.0.0.0` would expose it to your whole network. |
| `OLLAMA_KEEP_ALIVE` | `30m` | Keeps models loaded between calls. Loading a large model takes several seconds. |
| `OLLAMA_NUM_PARALLEL` | `2` | Concurrent requests per model. Each slot adds KV-cache memory. |
| `OLLAMA_MAX_LOADED_MODELS` | `2` | Agent model plus embedding model. The judge loads in a separate eval phase. |
| `OLLAMA_FLASH_ATTENTION` | `1` | Faster attention, less memory. |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Quantized KV cache, roughly half the memory of the default. |
| `OLLAMA_CONTEXT_LENGTH` | `32768` | Server-wide default context. Deskpilot also sets `num_ctx` on every request. |

## Models

| Model | Approx. size | Used for | Pull when |
|---|---|---|---|
| `qwen3.6:35b` | ~24 GB | Agent and injection guard | Milestone 1 |
| `qwen3:8b` | ~5 GB | Fast profile for iterating on graph logic | Milestone 1 |
| `nomic-embed-text` | < 1 GB | Embeddings for policy search | Milestone 1 |
| `gpt-oss:20b` | ~14 GB | Eval judge | Milestone 12 |

`qwen3.6:35b` is a mixture-of-experts model: only about 3B of its parameters are active per token, so it generates quickly. That matters because one ticket makes many sequential LLM calls.

Model choices are starting points. Milestone 12 compares candidates on the eval suite.

## Memory budget (48 GB)

| Consumer | Approx. |
|---|---|
| macOS, IDE, browser | 8–10 GB |
| Docker VM (Postgres and fake vendors) | ~6 GB |
| Agent model weights | ~24 GB |
| KV cache (32k context × 2 parallel slots, q8) | a few GB |

There's no room for the judge model at the same time as the agent, which is why evals run in two phases ([D-013](../decisions.md#d-013-evals-run-in-two-phases)).

## Switching models

For fast iteration where answer quality doesn't matter, set in `backend/.env`:

```bash
DESKPILOT_AGENT__MODEL=qwen3:8b
```

### Smaller machines

On 32 GB or less, use `qwen3:8b` for both the agent and guard:

```bash
DESKPILOT_AGENT__MODEL=qwen3:8b
DESKPILOT_GUARD__MODEL=qwen3:8b
```

## Things that bite

**Silent context truncation.** When a prompt exceeds the context window, Ollama drops the oldest tokens without an error. For an agent, that's usually the system prompt, including the instructions that tell the model to treat ticket text as data. That's why `num_ctx` is always set explicitly per role. A warning for prompts nearing the limit comes with token accounting in milestone 11.

**Cold starts.** The first call after loading can take several seconds. Deskpilot's timeouts (milestone 6) distinguish load time from a genuinely hung call.

**Thinking tokens.** `qwen3.6` can reason before answering. Thinking tokens count against the token budget and add latency, so thinking is configured per role with the `REASONING` setting. It is on for the agent only: without it, `qwen3.6` often says it will check a policy and then stops without calling the tool ([D-070](../decisions.md#d-070-thinking-is-on-for-the-agent-role)). If replies turn into promises with no tool calls, check that `DESKPILOT_AGENT__REASONING` has not been set to `false`.

## Verification

```bash
ollama list                 # pulled models
ollama show qwen3.6:35b     # capabilities, context length, quantization
ollama ps                   # loaded models, memory, CPU/GPU split
```

## Troubleshooting

**`ollama ps` shows CPU usage.** The model doesn't fully fit in GPU memory. In order of preference: close memory-heavy apps, lower Docker's memory limit, set `OLLAMA_NUM_PARALLEL=1`, or use a smaller model. macOS also limits how much unified memory the GPU may use (roughly three quarters of RAM by default); raising it with `sysctl` is possible but not needed for this setup.

**Responses are slow only on the first request.** That's model loading. Check `OLLAMA_KEEP_ALIVE` if it happens repeatedly.

**Port 11434 already in use.** Another Ollama instance is running. Find it with `lsof -i :11434`.
