# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for backend.services.stream_manager._calculate_scaled_dimensions."""

from __future__ import annotations

from backend.services import stream_manager as stream_manager_module
from backend.services.stream_manager import _calculate_scaled_dimensions


class TestCalculateScaledDimensions:
    def test_landscape_16_9_uses_16_9_preset(self):
        assert _calculate_scaled_dimensions(1920, 1080) == stream_manager_module.settings.SEGMENT_DIM_16_9

    def test_landscape_4_3_uses_4_3_preset(self):
        assert _calculate_scaled_dimensions(640, 480) == stream_manager_module.settings.SEGMENT_DIM_4_3

    def test_square_uses_1_1_preset(self):
        assert _calculate_scaled_dimensions(500, 500) == stream_manager_module.settings.SEGMENT_DIM_1_1

    def test_portrait_swaps_width_and_height_of_matching_preset(self):
        landscape_w, landscape_h = _calculate_scaled_dimensions(1920, 1080)
        portrait_w, portrait_h = _calculate_scaled_dimensions(1080, 1920)

        assert (portrait_w, portrait_h) == (landscape_h, landscape_w)

    def test_picks_nearest_preset_for_unusual_aspect_ratio(self, monkeypatch):
        # An ultra-wide source (21:9 ~= 2.33) is closer to 16:9 (1.78) than 4:3 (1.33) or 1:1.
        monkeypatch.setattr(stream_manager_module.settings, "SEGMENT_DIM_1_1", (100, 100))
        monkeypatch.setattr(stream_manager_module.settings, "SEGMENT_DIM_4_3", (120, 90))
        monkeypatch.setattr(stream_manager_module.settings, "SEGMENT_DIM_16_9", (160, 90))

        assert _calculate_scaled_dimensions(2100, 900) == (160, 90)

    def test_rounds_odd_preset_dimensions_down_to_even(self, monkeypatch):
        monkeypatch.setattr(stream_manager_module.settings, "SEGMENT_DIM_16_9", (577, 321))

        width, height = _calculate_scaled_dimensions(1920, 1080)

        assert width % 2 == 0
        assert height % 2 == 0
        assert (width, height) == (576, 320)
