# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for backend.services.stream_manager._calculate_scaled_dimensions."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.services import stream_manager as stream_manager_module
from backend.services.stream_manager import StreamManager
from backend.services.stream_manager import StreamHealth
from backend.services.stream_manager import _calculate_scaled_dimensions
from backend.services.stream_manager import _close_quietly
from backend.services.stream_manager import _encode_frame_jpeg_bytes


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

    def test_portrait_4_3_uses_matching_orientation(self):
        assert _calculate_scaled_dimensions(480, 640) == (384, 512)

    def test_matches_nearest_preset_for_around_1_33_ratio(self):
        assert _calculate_scaled_dimensions(800, 600) == stream_manager_module.settings.SEGMENT_DIM_4_3


class TestStreamManagerHelpers:
    def test_non_rtsp_input_has_no_options(self):
        mgr = StreamManager("stream-1", "file:///camera.mp4")

        assert mgr._input_options() == {}

    def test_source_fps_returns_zero_for_invalid_rate(self):
        class _FakeStream:
            average_rate = "invalid"

        assert StreamManager._source_fps(_FakeStream()) == 0.0

    def test_health_returns_independent_snapshot(self):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        mgr.health = StreamHealth(
            publishing=True,
            resolution="640x360",
            codec="h264",
            caption="Decision: No",
            caption_history=[{"response": "old"}],
            ttft_ms=5.0,
        )

        snapshot = mgr.get_health()
        snapshot.caption_history.append({"response": "mutated"})

        assert snapshot.publishing is True
        assert snapshot.resolution == "640x360"
        assert mgr.health.caption_history == [{"response": "old"}]

    def test_start_does_not_start_twice_without_prompt(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        started = []

        class _Thread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                started.append(self.kwargs["name"])

            def join(self, timeout):
                return None

        monkeypatch.setattr(stream_manager_module.threading, "Thread", _Thread)
        monkeypatch.setattr(mgr, "_stream_loop", lambda: None)

        mgr.start()
        mgr.start()

        assert started == ["stream-stream-1"]

    def test_close_quietly_swallows_close_error(self):
        class _BrokenContainer:
            def close(self):
                raise RuntimeError("close failed")

        _close_quietly(_BrokenContainer())

    def test_jpeg_encoder_returns_empty_for_missing_frame(self):
        assert _encode_frame_jpeg_bytes(None) == b""

    def test_stream_loop_returns_when_no_consumer_is_enabled(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        monkeypatch.setattr(stream_manager_module.settings, "WEBRTC_AUTO_PUBLISH", False)
        mgr._running = True

        mgr._stream_loop()

        assert mgr._running is True

    def test_open_relay_configures_h264_output(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        output = type("Output", (), {})()
        output.mux = lambda packets: None
        out_stream = type("OutStream", (), {"encode": lambda self, frame: []})()
        output.add_stream = lambda *args, **kwargs: out_stream
        monkeypatch.setattr(stream_manager_module.av, "open", lambda *args, **kwargs: output)

        result = mgr._open_relay(type("Stream", (), {"average_rate": 25})(), 640, 360)

        assert result == (output, out_stream.encode, output.mux)
        assert out_stream.width == 640
        assert out_stream.height == 360
        assert out_stream.gop_size == 50
        assert mgr.health.publishing is True

    def test_open_segment_writer_configures_sampling(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        output = type("Output", (), {})()
        output.mux = lambda packets: None
        out_stream = type("OutStream", (), {"encode": lambda self, frame: []})()
        output.add_stream = lambda *args, **kwargs: out_stream
        monkeypatch.setattr(stream_manager_module.av, "open", lambda *args, **kwargs: output)
        monkeypatch.setattr(stream_manager_module.settings, "FRAME_SAMPLE_FPS", 5.0)

        result = mgr._open_segment_writer(type("Stream", (), {"average_rate": 25})(), 640, 360, start_number=4)

        assert result.container is output
        assert result.avg_fps == 25.0
        assert result.sample_every == 5
        assert out_stream.width == 640

    def test_open_relay_uses_fallback_gop_without_fps(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        output = type("Output", (), {})()
        output.mux = lambda packets: None
        out_stream = type("OutStream", (), {"encode": lambda self, frame: []})()
        output.add_stream = lambda *args, **kwargs: out_stream
        monkeypatch.setattr(stream_manager_module.av, "open", lambda *args, **kwargs: output)

        mgr._open_relay(type("Stream", (), {"average_rate": None})(), 640, 360)

        assert out_stream.gop_size == stream_manager_module._RELAY_FALLBACK_GOP

    def test_open_segment_writer_returns_none_after_open_error(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        monkeypatch.setattr(stream_manager_module.av, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

        result = mgr._open_segment_writer(type("Stream", (), {"average_rate": 25})(), 640, 360)

        assert result is None
    def test_input_options_are_rtsp_tuned(self):
        mgr = StreamManager("stream-1", "rtsp://camera/live")

        assert mgr._input_options() == {
            "rtsp_transport": "tcp",
            "fflags": "nobuffer",
            "flags": "low_delay",
        }

    def test_source_fps_returns_zero_for_missing_rate(self):
        class _FakeStream:
            average_rate = None

        assert StreamManager._source_fps(_FakeStream()) == 0.0

    def test_delete_segment_removes_file_and_registry_entries(self, monkeypatch, tmp_path):
        segment = tmp_path / "old_segment.mp4"
        segment.write_bytes(b"x")
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        mgr.frame_registry = type("FrameRegistry", (), {"remove_segment": lambda self, sid, p: 3})()
        removed = {"count": 0}

        def fake_unlink(self, missing_ok=False):
            removed["count"] += 1

        monkeypatch.setattr(Path, "unlink", fake_unlink)
        mgr._delete_segment(str(segment))

        assert removed["count"] == 1

    def test_reclaim_old_segments_keeps_reserved_segments(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        mgr._finalized_segments = ["a.mp4", "b.mp4", "c.mp4"]
        monkeypatch.setattr(stream_manager_module.settings, "SEGMENT_MAX_ON_DISK", 2)
        monkeypatch.setattr(stream_manager_module, "get_deep_analyzer", lambda: type("DA", (), {"is_segment_active": lambda self, p: p == "b.mp4"})())
        deleted = []
        monkeypatch.setattr(mgr, "_delete_segment", lambda path: deleted.append(path))

        mgr._reclaim_old_segments("c.mp4")

        assert deleted == ["a.mp4", "c.mp4"]

    def test_notify_segment_finalized_and_reserved_state(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live")
        called = []
        monkeypatch.setattr(stream_manager_module.settings, "DEEP_ANALYZER_ENABLED", True)
        monkeypatch.setattr(stream_manager_module, "get_deep_analyzer", lambda: type("DA", (), {"on_segment_finalized": lambda self, p: called.append(p)})())

        mgr._notify_segment_finalized("z.mp4")

        assert called == ["z.mp4"]

    def test_infer_worker_handles_yes_verdict_and_triggers_deep_analysis(self, monkeypatch):
        mgr = StreamManager("stream-1", "rtsp://camera/live", vlm_prompt="prompt", alert_event="fire")
        mgr._running = True
        mgr._playback_start_ts = 0.0
        mgr._latest_frame = __import__("numpy").zeros((2, 2, 3), dtype="uint8")
        mgr._latest_frame_ts = 1.0
        mgr._latest_frame_id = "frame-1"
        mgr.frame_registry = type("Registry", (), {"get_segment": lambda self, fid: "segments/seg_001.mp4"})()

        class _FakeEvent:
            def __init__(self, manager):
                self._manager = manager

            def wait(self, timeout=0.5):
                self._manager._running = False
                return True

            def clear(self):
                return None

        class _FakeEngine:
            def caption_with_metrics(self, frame, prompt=None, priority=False):
                return "Decision: Yes\nDescription: Visible fire.", {"ttft_ms": 10.0, "tpot_ms": 2.0, "throughput_tps": 12.0, "total_tokens_generated": 21.0}

        called = []

        class _FakeDA:
            def submit(self, **kwargs):
                called.append(kwargs["segment_path"])

        monkeypatch.setattr(stream_manager_module, "get_vlm_engine", lambda: _FakeEngine())
        monkeypatch.setattr(stream_manager_module, "parse_yes_no", lambda caption: True)
        monkeypatch.setattr(stream_manager_module, "get_deep_analyzer", lambda: _FakeDA())
        monkeypatch.setattr(stream_manager_module, "_encode_frame_jpeg_bytes", lambda frame: b"jpeg")
        mgr._frame_event = _FakeEvent(mgr)

        mgr._infer_worker()

        assert called == ["segments/seg_001.mp4"]
