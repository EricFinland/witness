from witness.pricing import calculate_cost


def test_known_anthropic_model():
    # Sonnet 4.5: $3/$15 per Mtok. 1M in + 500k out → 3 + 7.5 = 10.5
    assert calculate_cost("claude-sonnet-4-5", 1_000_000, 500_000) == 10.5


def test_known_openai_model():
    # gpt-4o: $2.50/$10 per Mtok. 2M in + 1M out → 5 + 10 = 15
    assert calculate_cost("gpt-4o", 2_000_000, 1_000_000) == 15.0


def test_unknown_model_returns_zero():
    assert calculate_cost("fictional-model-9000", 1000, 1000) == 0.0


def test_provider_prefix_stripped():
    assert calculate_cost("anthropic/claude-sonnet-4-5", 1_000_000, 0) == 3.0


def test_empty_model():
    assert calculate_cost("", 1000, 1000) == 0.0


def test_zero_tokens():
    assert calculate_cost("claude-sonnet-4-5", 0, 0) == 0.0
