# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for backend.services.utils._extract_perf_metrics."""

from __future__ import annotations

from backend.services import utils


class _Stat:
    def __init__(self, mean):
        self.mean = mean
        self.std = 0.0


class _FakePerfMetrics:
    def __init__(self, ttft=1.0, tpot=2.0, throughput=3.0):
        self._ttft = ttft
        self._tpot = tpot
        self._throughput = throughput

    def get_load_time(self):
        return 0.0

    def get_num_generated_tokens(self):
        return 10

    def get_num_input_tokens(self):
        return 5

    def get_ttft(self):
        return _Stat(self._ttft)

    def get_tpot(self):
        return _Stat(self._tpot)

    def get_throughput(self):
        return _Stat(self._throughput)

    def get_inference_duration(self):
        return _Stat(0.0)

    def get_generate_duration(self):
        return _Stat(0.0)


class _BrokenPerfMetrics(_FakePerfMetrics):
    def get_ttft(self):
        raise RuntimeError("perf_metrics API drift")


class _FakeResult:
    def __init__(self, perf_metrics):
        self.perf_metrics = perf_metrics


class TestExtractPerfMetrics:
    def test_returns_none_values_when_perf_metrics_missing(self):
        result = object()  # no `perf_metrics` attribute at all
        metrics = utils._extract_perf_metrics(result)
        assert metrics == {"ttft_ms": None, "tpot_ms": None, "throughput_tps": None}

    def test_extracts_mean_values_from_perf_metrics(self):
        result = _FakeResult(_FakePerfMetrics(ttft=12.5, tpot=3.25, throughput=42.0))

        metrics = utils._extract_perf_metrics(result)

        assert metrics == {"ttft_ms": 12.5, "tpot_ms": 3.25, "throughput_tps": 42.0}

    def test_falls_back_to_none_when_accessors_raise(self):
        result = _FakeResult(_BrokenPerfMetrics())

        metrics = utils._extract_perf_metrics(result)

        assert metrics == {"ttft_ms": None, "tpot_ms": None, "throughput_tps": None}
