# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for backend.services.vlm.parse_yes_no."""

from __future__ import annotations

import pytest
from backend.services.vlm import parse_yes_no


class TestParseYesNo:
    @pytest.mark.parametrize(
        "caption",
        ["Yes", "  YES, definitely.  ", "yes-it-is", "Yes."],
    )
    def test_recognizes_affirmative_captions(self, caption):
        assert parse_yes_no(caption) is True

    @pytest.mark.parametrize(
        "caption",
        ["No", "no.", "No, nothing here.", "NO!"],
    )
    def test_recognizes_negative_captions(self, caption):
        assert parse_yes_no(caption) is False

    @pytest.mark.parametrize("caption", ["Maybe", "Unclear", "123", "", None])
    def test_returns_none_for_ambiguous_captions(self, caption):
        assert parse_yes_no(caption) is None

    def test_known_quirk_no_prefixed_words_are_treated_as_negative(self):
        # "Not sure" normalizes to "notsure", which starts with "no" — the
        # parser only looks at the leading prefix, not whole-word matching.
        assert parse_yes_no("Not sure") is False
