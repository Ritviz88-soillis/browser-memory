"""Chat model factory: the one place that decides which LLM the server uses.

Groq (Llama 3.3) when GROQ_API_KEY is set, otherwise Google Gemini. Both are
free tiers that need no credit card. Services build their own chain on top:
``prompt | build_chat_model(...) | StrOutputParser()``.
"""

import os

from langchain_core.language_models.chat_models import BaseChatModel

import config


def uses_groq() -> bool:
    """Whether a Groq key is configured (Groq is preferred when present)."""

    return bool(os.environ.get("GROQ_API_KEY"))


def chat_model_name() -> str:
    """Name of the chat model in use, recorded with every logged query."""

    return config.GROQ_MODEL if uses_groq() else config.GEMINI_MODEL


def build_chat_model(temperature: float) -> BaseChatModel:
    """Create the chat model for a chain.

    Args:
        temperature: Sampling temperature for this chain.

    Returns:
        A LangChain chat model (Groq if configured, else Gemini).

    Raises:
        RuntimeError: If neither GROQ_API_KEY nor GOOGLE_API_KEY is set.
    """

    if uses_groq():
        from langchain_groq import ChatGroq

        return ChatGroq(model=config.GROQ_MODEL, temperature=temperature)

    if not os.environ.get("GOOGLE_API_KEY"):
        raise RuntimeError("Set GROQ_API_KEY or GOOGLE_API_KEY in server/.env")

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=config.GEMINI_MODEL, temperature=temperature)
