"""LLM client factory.

Switches between Anthropic Claude (default) and OpenAI based on env var
`LLM_PROVIDER`. Models are configurable per role (writer / reviewer) so they
can be tuned independently.

For Anthropic, system prompts are sent with cache_control so multi-turn
revision loops benefit from prompt caching.
"""

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

DEFAULT_PROVIDER = "anthropic"
DEFAULT_WRITER_MODEL = "claude-sonnet-4-6"
DEFAULT_REVIEWER_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).lower()


def _build_llm(role: str) -> BaseChatModel:
    provider = _provider()
    if provider == "anthropic":
        if role == "writer":
            model = os.environ.get("WRITER_MODEL", DEFAULT_WRITER_MODEL)
        else:
            model = os.environ.get("REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL)
        return ChatAnthropic(model=model, max_tokens=4096)
    if provider == "openai":
        model = os.environ.get(f"{role.upper()}_MODEL", DEFAULT_OPENAI_MODEL)
        return ChatOpenAI(model=model)
    raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


def writer_llm() -> BaseChatModel:
    return _build_llm("writer")


def reviewer_llm() -> BaseChatModel:
    return _build_llm("reviewer")
