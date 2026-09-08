# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for pure helpers in backend.services.deep_analyzer.

DeepAnalyzerEngine.__init__ loads a real VLM pipeline, so these tests only
exercise the module-level function and static method that don't require
instantiating the engine.
"""

from __future__ import annotations

from collections import OrderedDict

import pytest
from backend.services import deep_analyzer as deep_analyzer_module
from backend.services.deep_analyzer import DeepAnalyzerEngine
from backend.services.deep_analyzer import _next_segment_path


class TestNextSegmentPath:
    def test_increments_the_trailing_index(self):
        assert _next_segment_path("segments/default_segment_0001.mp4") == "segments/default_segment_0002.mp4"

    def test_preserves_zero_padding_width(self):
        assert _next_segment_path("segments/default_segment_0009.mp4") == "segments/default_segment_0010.mp4"

    def test_does_not_truncate_when_index_grows_a_digit(self):
        assert _next_segment_path("segments/default_segment_9999.mp4") == "segments/default_segment_10000.mp4"

    def test_returns_none_when_path_has_no_digits(self):
        assert _next_segment_path("segments/default_segment.mp4") is None


class TestMarkBoundedCache:
    def test_adds_key_to_cache(self):
        cache: "OrderedDict[str, None]" = OrderedDict()
        DeepAnalyzerEngine._mark(cache, "segment-1")
        assert list(cache.keys()) == ["segment-1"]

    def test_evicts_oldest_key_once_over_capacity(self, monkeypatch):
        monkeypatch.setattr(deep_analyzer_module.settings, "DEEP_ANALYZER_DEDUP_CACHE_SIZE", 2)
        cache: "OrderedDict[str, None]" = OrderedDict()

        DeepAnalyzerEngine._mark(cache, "segment-1")
        DeepAnalyzerEngine._mark(cache, "segment-2")
        DeepAnalyzerEngine._mark(cache, "segment-3")

        assert list(cache.keys()) == ["segment-2", "segment-3"]

    @pytest.mark.parametrize("capacity", [1, 5])
    def test_never_exceeds_configured_capacity(self, monkeypatch, capacity):
        monkeypatch.setattr(deep_analyzer_module.settings, "DEEP_ANALYZER_DEDUP_CACHE_SIZE", capacity)
        cache: "OrderedDict[str, None]" = OrderedDict()

        for i in range(capacity + 10):
            DeepAnalyzerEngine._mark(cache, f"segment-{i}")

        assert len(cache) == capacity
