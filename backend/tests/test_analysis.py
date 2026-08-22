from app.services.analysis import _sampling_kwargs


def test_sampling_kwargs_omit_temperature_for_gpt5():
    assert _sampling_kwargs("gpt-5") == {}
    assert _sampling_kwargs("gpt-5-mini") == {}
    assert _sampling_kwargs("gpt-4.1-mini") == {"temperature": 0.2}
