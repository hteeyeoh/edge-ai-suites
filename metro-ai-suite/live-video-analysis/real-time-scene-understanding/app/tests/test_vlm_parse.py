# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for backend.services.vlm helpers."""

from __future__ import annotations

import pytest
from backend.services.vlm import parse_yes_no


class TestParseYesNo:
    @pytest.mark.parametrize(
        "caption",
        [
            "Yes",
            "  YES, definitely.  ",
            "yes-it-is",
            "Yes.",
            "Decision: Yes\nDescription: Person holding an item near shelf.",
        ],
    )
    def test_recognizes_affirmative_captions(self, caption):
        assert parse_yes_no(caption) is True

    @pytest.mark.parametrize(
        "caption",
        [
            "No",
            "no.",
            "No, nothing here.",
            "NO!",
            "Decision: No\nDescription: Regular shopping scene with no suspicious act.",
        ],
    )
    def test_recognizes_negative_captions(self, caption):
        assert parse_yes_no(caption) is False

    @pytest.mark.parametrize(
        "caption",
        [
            "Maybe",
            "Unclear",
            "123",
            "",
            None,
            "There is no direct evidence, but yes there is suspicious movement.",
        ],
    )
    def test_returns_none_for_ambiguous_captions(self, caption):
        assert parse_yes_no(caption) is None

    def test_prefers_decision_field_over_other_text(self):
        caption = "No obvious event. Decision: Yes\nDescription: Person conceals item in bag."
        assert parse_yes_no(caption) is True
