"""Rough token accounting for the conversation context.

Ollama does not expose a tokenizer, and pulling one in per model would mean a
heavy dependency and a download per model. The context budget therefore uses an
estimate, deliberately biased to overcount: trimming one message too many costs
nothing, while undercounting silently truncates the prompt inside the model.
"""

# Latin text averages ~4 characters per token; Cyrillic and other non-ASCII
# scripts fragment far more, closer to ~2.
CHARS_PER_TOKEN_ASCII = 3.5
CHARS_PER_TOKEN_OTHER = 2.0

# Every message carries role markers and separators in the chat template.
TOKENS_PER_MESSAGE_OVERHEAD = 4


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a piece of text."""
    if not text:
        return 0

    ascii_chars = sum(1 for char in text if char.isascii())
    other_chars = len(text) - ascii_chars

    estimate = ascii_chars / CHARS_PER_TOKEN_ASCII + other_chars / CHARS_PER_TOKEN_OTHER
    return max(1, round(estimate))


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate the token cost of a whole message list."""
    return sum(
        estimate_tokens(message.get("content", "")) + TOKENS_PER_MESSAGE_OVERHEAD
        for message in messages
    )
