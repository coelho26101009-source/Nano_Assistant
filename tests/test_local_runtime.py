from core.local_runtime import choose_model


def test_explicit_model_wins():
    profile = choose_model({"local": {"model": "qwen2.5:0.5b-instruct"}})
    assert profile.model == "qwen2.5:0.5b-instruct"


def test_auto_returns_supported_profile():
    profile = choose_model({"local": {"model": "auto"}})
    assert profile.model in {
        "qwen2.5:0.5b-instruct",
        "qwen2.5:1.5b-instruct",
        "qwen2.5:3b",
    }
    assert profile.ram_gb > 0
