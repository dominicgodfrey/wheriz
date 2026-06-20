"""LLM edge layer: parses user language in, phrases reasons out — never ranks.

All inference is local (Ollama). The deterministic engine (``wwiw.engine``) decides;
this layer only translates between natural language and the engine's vocabulary. Every
call is logged to ``data/llm_logs/`` (gitignored). Prompt templates live in
``prompts/`` — one file per task, never inlined in business logic.
"""

from .client import LLMClient, LLMError, OllamaClient, OllamaConfig

__all__ = ["LLMClient", "LLMError", "OllamaClient", "OllamaConfig"]
