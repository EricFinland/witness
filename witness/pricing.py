"""Model pricing table. Prices are USD per 1M tokens."""

from __future__ import annotations

import warnings

# (input_per_mtok, output_per_mtok)
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic — Claude 4.x
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00),
    "claude-opus-4-5": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-2024-08-06": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
}

_warned: set[str] = set()


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a call. Unknown models → 0 (with one-time warning)."""
    if not model:
        return 0.0

    key = _normalize(model)
    entry = PRICES.get(key)
    if entry is None:
        if key not in _warned:
            _warned.add(key)
            warnings.warn(
                f"witness: unknown model pricing for {model!r}; cost reported as $0.",
                stacklevel=2,
            )
        return 0.0

    in_rate, out_rate = entry
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def _normalize(model: str) -> str:
    """Best-effort match: exact first, then strip provider prefix, then strip date suffix."""
    if model in PRICES:
        return model
    # "anthropic/claude-sonnet-4-5" → "claude-sonnet-4-5"
    if "/" in model:
        tail = model.split("/", 1)[1]
        if tail in PRICES:
            return tail
        model = tail
    return model
