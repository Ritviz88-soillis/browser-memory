"""Chat model factory: the one place that decides which LLM the server uses.

Three providers, all with free tiers that need no credit card:

    groq         Llama 3.3 70B on Groq            (GROQ_API_KEY)
    huggingface  an open model on Hugging Face    (HUGGINGFACEHUB_API_TOKEN)
    gemini       Google Gemini                    (GOOGLE_API_KEY)

Set LLM_PROVIDER in .env to choose one; otherwise the first provider with a
key is used, in the order above. Services build their own chain on top:
``prompt | build_chat_model(...) | StrOutputParser()``.
"""

import os

from langchain_core.language_models.chat_models import BaseChatModel

import config

_KEY_FOR_PROVIDER = {
    "groq": "GROQ_API_KEY",
    "huggingface": "HUGGINGFACEHUB_API_TOKEN",
    "gemini": "GOOGLE_API_KEY",
}


class LLMUnavailable(RuntimeError):
    """The language model could not answer; the message is safe to show the user."""


def provider() -> str:
    """Which provider is in use.

    Returns:
        The provider named by LLM_PROVIDER, else the first one with a key.

    Raises:
        RuntimeError: If LLM_PROVIDER is not a known name, or no key is set.
    """

    chosen = config.LLM_PROVIDER
    if chosen:
        if chosen not in _KEY_FOR_PROVIDER:
            raise RuntimeError(
                f"LLM_PROVIDER={chosen!r} is not one of {sorted(_KEY_FOR_PROVIDER)}"
            )
        return chosen

    for name, key in _KEY_FOR_PROVIDER.items():
        if os.environ.get(key):
            return name

    raise RuntimeError(
        "Set GROQ_API_KEY, HUGGINGFACEHUB_API_TOKEN or GOOGLE_API_KEY in server/.env"
    )


def chat_model_name() -> str:
    """Name of the chat model in use, recorded with every logged query."""

    return {
        "groq": config.GROQ_MODEL,
        "huggingface": config.HF_MODEL,
        "gemini": config.GEMINI_MODEL,
    }[provider()]


def describe_llm_error(error: Exception) -> str:
    """Turn a provider error into a sentence the user can act on.

    Args:
        error: The exception raised by the chat model call.

    Returns:
        A short explanation, naming the free usage limit when that is the cause.
    """

    text = str(error).lower()
    if any(sign in text for sign in ("429", "402", "quota", "rate limit", "rate_limit", "credits")):
        return (
            f"The language model ({chat_model_name()}) has hit its free usage limit. "
            "It resets on its own; to keep going now, switch LLM_PROVIDER in "
            "server/.env. Your memory is unaffected, and related pages still work."
        )
    return (
        f"The language model ({chat_model_name()}) could not be reached. "
        "Check the internet connection and try again."
    )


def build_chat_model(temperature: float) -> BaseChatModel:
    """Create the chat model for a chain.

    Args:
        temperature: Sampling temperature for this chain.

    Returns:
        A LangChain chat model for the provider in use.

    Raises:
        RuntimeError: If the chosen provider's key is missing.
    """

    name = provider()
    if not os.environ.get(_KEY_FOR_PROVIDER[name]):
        raise RuntimeError(f"{_KEY_FOR_PROVIDER[name]} is not set in server/.env")

    if name == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=config.GROQ_MODEL, temperature=temperature)

    if name == "huggingface":
        from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

        endpoint = HuggingFaceEndpoint(
            repo_id=config.HF_MODEL,
            task="text-generation",
            # the endpoint rejects a temperature of exactly zero
            temperature=max(temperature, 0.01),
            max_new_tokens=config.HF_MAX_NEW_TOKENS,
        )
        return ChatHuggingFace(llm=endpoint)

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=config.GEMINI_MODEL, temperature=temperature)
