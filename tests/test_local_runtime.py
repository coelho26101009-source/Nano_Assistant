from core.local_runtime import choose_model


SUPPORTED_MODELS = {
    "qwen2.5:0.5b",
    "qwen2.5:1.5b",
    "qwen2.5:3b",
}


def test_explicit_model_wins():
    profile = choose_model({"local": {"model": "qwen2.5:0.5b"}})
    assert profile.model == "qwen2.5:0.5b"


def test_auto_returns_supported_profile():
    profile = choose_model({"local": {"model": "auto"}})
    assert profile.model in SUPPORTED_MODELS
    assert profile.ram_gb > 0


def test_legacy_instruct_tag_is_not_selected():
    profile = choose_model({"local": {"model": "auto"}})
    assert "instruct" not in profile.model
