# Provider support status

| Provider | Status | Notes |
|---|---|---|
| OpenRouter | Implemented | OpenAI-compatible `/models` and `/chat/completions`; key via `OPENROUTER_API_KEY`. |
| OpenAI | Implemented | `/models` and `/chat/completions`; key via `OPENAI_API_KEY`. |
| OpenAI-compatible endpoint | Implemented | Configure `COMPATIBLE_API_BASE_URL`, optional key and model. Can target compatible local inference servers. |
| Ollama / LM Studio / vLLM / llama.cpp | Compatible-endpoint path | Works only when the selected server exposes compatible routes and request formats; not separately integration-tested here. |
| Anthropic / Gemini / DeepSeek / Mistral / xAI | Not all natively implemented | Some endpoints may be usable through compatible gateways, but no native adapter is claimed. |

Streaming and tool calling are currently disabled. Usage is returned only when supplied by the provider; price calculations are not implemented.
