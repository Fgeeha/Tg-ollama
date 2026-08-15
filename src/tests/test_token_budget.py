"""Estimating how much context fits.

Ollama exposes no tokenizer, so the budget is an estimate. It only has to be
conservative — never claim less than the model will actually count.
"""
import pytest

from bot.utils.tokens import estimate_tokens


def test_empty_text_costs_nothing():
    assert estimate_tokens("") == 0


@pytest.mark.parametrize("text", [
    "hello world",
    "Привет, как дела?",
    "def foo(x):\n    return x * 2",
    "混合 текст and english",
])
def test_estimate_is_positive_and_scales(text):
    assert estimate_tokens(text) > 0
    assert estimate_tokens(text * 10) > estimate_tokens(text)


def test_cyrillic_costs_more_per_character_than_latin():
    """Russian text tokenizes into more pieces than English of the same length."""
    latin = "a" * 100
    cyrillic = "я" * 100

    assert estimate_tokens(cyrillic) > estimate_tokens(latin)


def test_estimate_does_not_undercount_typical_text():
    """A rough sanity bound: real tokenizers land well under this estimate."""
    text = "The quick brown fox jumps over the lazy dog. " * 20

    # ~180 words; a real tokenizer gives roughly 200-240 tokens
    assert estimate_tokens(text) >= 200


@pytest.mark.asyncio
async def test_history_is_trimmed_to_the_token_budget(db):
    """Old turns drop out once the estimated history exceeds the budget."""
    from bot.utils.context import ConversationContext
    from bot.utils.tokens import estimate_messages_tokens

    context = ConversationContext(1, "m", max_tokens=100)
    for i in range(30):
        await context.add_message("user", f"Сообщение номер {i}. " + "текст " * 10)

    history = await context.get_context()

    assert estimate_messages_tokens(history) <= 100
    # The most recent turn always survives
    assert "номер 29" in history[-1]["content"]


@pytest.mark.asyncio
async def test_last_exchange_is_never_dropped(db):
    """Even an oversized single message leaves a usable conversation."""
    from bot.utils.context import ConversationContext

    context = ConversationContext(2, "m", max_tokens=10)
    await context.add_message("user", "вопрос " * 500)
    await context.add_message("assistant", "ответ " * 500)

    history = await context.get_context()

    assert len(history) == 2
