# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for backend.config: env parsing helpers and prompt builders."""

from __future__ import annotations

import pytest
from backend.config import _bool
from backend.config import _build_event_prompt
from backend.config import _float
from backend.config import _frame_size
from backend.config import _frame_size_default
from backend.config import _int
from backend.config import build_alert_prompt
from backend.config import build_deep_analyzer_prompt


class TestIntFloatHelpers:
    def test_int_parses_valid_env_value(self, monkeypatch):
        monkeypatch.setenv("MY_INT", "42")
        assert _int("MY_INT", 7) == 42

    def test_int_falls_back_to_default_on_invalid_value(self, monkeypatch):
        monkeypatch.setenv("MY_INT", "not-a-number")
        assert _int("MY_INT", 7) == 7

    def test_int_uses_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_INT", raising=False)
        assert _int("MY_INT", 7) == 7

    def test_float_parses_valid_env_value(self, monkeypatch):
        monkeypatch.setenv("MY_FLOAT", "1.5")
        assert _float("MY_FLOAT", 0.0) == 1.5

    def test_float_falls_back_to_default_on_invalid_value(self, monkeypatch):
        monkeypatch.setenv("MY_FLOAT", "nope")
        assert _float("MY_FLOAT", 2.5) == 2.5


class TestBoolHelper:
    @pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE", "yes", "Yes"])
    def test_bool_true_values(self, monkeypatch, raw):
        monkeypatch.setenv("MY_BOOL", raw)
        assert _bool("MY_BOOL", False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "garbage"])
    def test_bool_false_values(self, monkeypatch, raw):
        monkeypatch.setenv("MY_BOOL", raw)
        assert _bool("MY_BOOL", True) is False

    def test_bool_uses_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_BOOL", raising=False)
        assert _bool("MY_BOOL", True) is True


class TestFrameSizeHelper:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("640x360", (640, 360)),
            ("640X360", (640, 360)),
            ("640,360", (640, 360)),
            (" 640 x 360 ", (640, 360)),
        ],
    )
    def test_frame_size_parses_valid_formats(self, monkeypatch, raw, expected):
        monkeypatch.setenv("MY_SIZE", raw)
        assert _frame_size("MY_SIZE") == expected

    def test_frame_size_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_SIZE", raising=False)
        assert _frame_size("MY_SIZE") is None

    @pytest.mark.parametrize("raw", ["notasize", "640x", "x360", "640-360"])
    def test_frame_size_returns_none_on_invalid_format(self, monkeypatch, raw):
        monkeypatch.setenv("MY_SIZE", raw)
        assert _frame_size("MY_SIZE") is None

    @pytest.mark.parametrize("raw", ["0x360", "640x0"])
    def test_frame_size_returns_none_on_non_positive_dimensions(self, monkeypatch, raw):
        monkeypatch.setenv("MY_SIZE", raw)
        assert _frame_size("MY_SIZE") is None

    def test_frame_size_default_falls_back_when_invalid(self, monkeypatch):
        monkeypatch.setenv("MY_SIZE", "invalid")
        assert _frame_size_default("MY_SIZE", (100, 200)) == (100, 200)

    def test_frame_size_default_uses_parsed_value_when_valid(self, monkeypatch):
        monkeypatch.setenv("MY_SIZE", "300x400")
        assert _frame_size_default("MY_SIZE", (100, 200)) == (300, 400)


class TestBuildEventPrompt:
    def test_substitutes_event_into_template(self):
        assert _build_event_prompt("Look for {event}.", "fire") == "Look for fire."

    def test_normalizes_internal_whitespace(self):
        assert _build_event_prompt("{event}", "  a   fire   truck  ") == "a fire truck"

    @pytest.mark.parametrize("empty_event", ["", "   ", None])
    def test_raises_on_empty_event(self, empty_event):
        with pytest.raises(ValueError, match="must not be empty"):
            _build_event_prompt("{event}", empty_event)

    @pytest.mark.parametrize("multi_event", ["fire, smoke", "fire;smoke", "fire|smoke", "fire/smoke"])
    def test_raises_when_multiple_events_supplied(self, multi_event):
        with pytest.raises(ValueError, match="Only one alert event"):
            _build_event_prompt("{event}", multi_event)

    def test_raises_when_template_missing_placeholder(self):
        with pytest.raises(ValueError, match="must include '{event}'"):
            _build_event_prompt("references {other} not the expected key", "fire")


class TestPromptBuilders:
    def test_build_alert_prompt_includes_event(self):
        prompt = build_alert_prompt("fire")
        assert "fire" in prompt
        assert prompt.strip().endswith('"Yes" or "No".')

    def test_build_deep_analyzer_prompt_includes_event(self):
        prompt = build_deep_analyzer_prompt("fire")
        assert "fire" in prompt

    def test_build_alert_prompt_rejects_empty_event(self):
        with pytest.raises(ValueError):
            build_alert_prompt("")
