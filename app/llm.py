"""One function that returns a chat model. Anthropic is the default; OpenAI/Groq are optional."""
from app import config

KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY"}
EXTRA_PACKAGES = {"openai": "langchain-openai", "groq": "langchain-groq"}


class MissingAPIKey(RuntimeError):
    pass


def get_llm(model=None):
    model = model or config.MODEL
    provider = config.PROVIDER
    key_name = KEY_NAMES.get(provider)
    if key_name is None:
        raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use anthropic, openai or groq.")
    key = getattr(config, key_name)
    if not key:
        raise MissingAPIKey(
            f"No API key for LLM_PROVIDER='{provider}'. Put {key_name}=... in .env "
            f"(local) or in the AWS Secrets Manager secret 'insightly/app' (EKS).")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        # max_tokens caps answer length (and worst-case cost); retries cover 429/529 overloads
        return ChatAnthropic(model=model, api_key=key, max_tokens=config.LLM_MAX_TOKENS,
                             max_retries=3, timeout=config.LLM_TIMEOUT_S)
    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model, api_key=key, timeout=config.LLM_TIMEOUT_S, max_retries=3)
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, api_key=key, timeout=config.LLM_TIMEOUT_S, max_retries=3)
    except ImportError as e:
        raise ImportError(f"pip install {EXTRA_PACKAGES[provider]} to use LLM_PROVIDER={provider}") from e
