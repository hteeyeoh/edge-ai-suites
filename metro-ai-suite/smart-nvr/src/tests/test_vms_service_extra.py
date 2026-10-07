"""Extra tests for VmsService covering success and failure paths."""
import pytest
from unittest.mock import patch, MagicMock
from service.vms_service import VmsService

class DummyAsyncStream:
    def __init__(self, chunks):
        async def gen():
            for c in chunks:
                yield c
        # body_iterator is an async iterable object directly (accessed without call)
        self.body_iterator = gen()


def test_build_camera_candidates_skips_synthetic_region_name():
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    candidates = vs._build_camera_candidates(
        "si1-region-camera4", ["si1-camera1", "si1-camera2", "si1-camera3", "si1-camera4"]
    )
    assert candidates[0] == "si1-camera4"
    assert "si1-region-camera4" not in candidates


def test_build_camera_candidates_keeps_real_camera_name():
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    candidates = vs._build_camera_candidates("si1-camera1", ["si1-camera1", "si1-camera2"])
    assert candidates[0] == "si1-camera1"

@pytest.mark.asyncio
async def test_upload_video_to_summarizer_success(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_summary_url = "http://dummy-summary"
    vs.vss_search_url = "http://dummy-search"
    dummy_stream = DummyAsyncStream([b"x" * 200])
    vs.frigate_service.get_clip_from_timestamps.return_value = dummy_stream
    vs.summarization_service.video_upload.return_value = {"videoId": "vid123"}
    resp = await vs.upload_video_to_summarizer("cam1", 1.0, 2.0, False)
    assert resp["status"] == 200
    assert resp["message"] == "vid123"

@pytest.mark.asyncio
async def test_upload_video_to_summarizer_small_file(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_summary_url = "http://dummy-summary"
    dummy_stream = DummyAsyncStream([b"x" * 10])  # too small triggers 404
    vs.frigate_service.get_clip_from_timestamps.return_value = dummy_stream
    resp = await vs.upload_video_to_summarizer("cam1", 1.0, 2.0, False)
    assert resp["status"] == 404

@pytest.mark.asyncio
async def test_summarize_pipeline_failure(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_summary_url = "http://dummy-summary"
    # upload succeeds
    dummy_stream = DummyAsyncStream([b"x" * 200])
    vs.frigate_service.get_clip_from_timestamps.return_value = dummy_stream
    vs.summarization_service.video_upload.return_value = {"videoId": "vid999"}
    vs.summarization_service.create_summary.return_value = {}  # missing pipeline id
    resp = await vs.summarize("cam1", 1.0, 2.0)
    assert resp["status"] == 500

def test_summary_empty_result(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_summary_url = "http://dummy-summary"
    # The VmsService.summary method calls the module-level summarization_service, not the instance attribute.
    # Patch the global to avoid an HTTP request and return a controlled empty summary result.
    monkeypatch.setattr(
        "service.vms_service.summarization_service.get_summary_result",
        lambda pipeline_id, base_url: {
            "frameSummaries": [
                {
                    "startFrame": 0,
                    "endFrame": 10,
                    "status": "ok",
                    "summary": None,
                }
            ],
            "summary": None,
        },
    )
    out = vs.summary("sum123")
    assert "Final summary is being generated" in out["summary"]
    assert out["frameSummaries"][0]["status"] == "ok"

@pytest.mark.asyncio
async def test_search_embeddings_success(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_search_url = "http://dummy-search"
    vs.vss_summary_url = "http://dummy-summary"
    dummy_stream = DummyAsyncStream([b"x" * 200])
    vs.frigate_service.get_clip_from_timestamps.return_value = dummy_stream
    vs.summarization_service.video_upload.return_value = {"videoId": "vidAB"}
    # Patch requests.post inside module
    with patch("service.vms_service.requests.post", return_value=MagicMock(json=lambda: {"message": "ok"}, raise_for_status=lambda: None)):
        resp = await vs.search_embeddings("cam1", 1.0, 2.0)
        assert resp["status"] == 200
        assert resp["video_id"] == "vidAB"


@pytest.mark.asyncio
async def test_upload_video_to_summarizer_waits_for_recent_window_without_shrinking(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_summary_url = "http://dummy-summary"

    dummy_stream = DummyAsyncStream([b"x" * 200])
    vs.frigate_service.get_clip_from_timestamps.return_value = dummy_stream
    vs.summarization_service.video_upload.return_value = {"videoId": "vidXYZ"}

    monkeypatch.setattr("service.vms_service.time.time", lambda: 100.0)
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("service.vms_service.asyncio.sleep", fake_sleep)

    resp = await vs.upload_video_to_summarizer("cam1", 80.0, 101.0, False, apply_end_buffer=True)
    assert resp["status"] == 200
    assert resp["message"] == "vidXYZ"

    # end=101 + 2s buffer = 103 ready_at; now=100 -> wait 3s (not clamped/truncated).
    assert sleep_calls == [pytest.approx(3.0, abs=1e-6)]

    call = vs.frigate_service.get_clip_from_timestamps.call_args
    assert call.args[0] == "cam1"
    assert call.args[1] == pytest.approx(80.0, abs=1e-6)
    assert call.args[2] == pytest.approx(101.0, abs=1e-6)
    assert call.kwargs["download"] is True


@pytest.mark.asyncio
async def test_upload_video_to_summarizer_caps_wait_duration(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_summary_url = "http://dummy-summary"

    dummy_stream = DummyAsyncStream([b"x" * 200])
    vs.frigate_service.get_clip_from_timestamps.return_value = dummy_stream
    vs.summarization_service.video_upload.return_value = {"videoId": "vidXYZ"}

    monkeypatch.setattr("service.vms_service.time.time", lambda: 100.0)
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("service.vms_service.asyncio.sleep", fake_sleep)

    # end=150 is far in the future; wait should be capped at FRIGATE_CLIP_MAX_WAIT_SECONDS (5s).
    resp = await vs.upload_video_to_summarizer("cam1", 80.0, 150.0, False, apply_end_buffer=True)
    assert resp["status"] == 200
    assert sleep_calls == [pytest.approx(5.0, abs=1e-6)]


@pytest.mark.asyncio
async def test_upload_video_to_summarizer_does_not_clamp_when_disabled(monkeypatch):
    vs = VmsService(frigate_service=MagicMock(), summarization_service=MagicMock())
    vs.vss_summary_url = "http://dummy-summary"

    dummy_stream = DummyAsyncStream([b"x" * 200])
    vs.frigate_service.get_clip_from_timestamps.return_value = dummy_stream
    vs.summarization_service.video_upload.return_value = {"videoId": "vidXYZ"}

    monkeypatch.setattr("service.vms_service.time.time", lambda: 100.0)

    resp = await vs.upload_video_to_summarizer("cam1", 80.0, 120.0, False, apply_end_buffer=False)
    assert resp["status"] == 200
    assert resp["message"] == "vidXYZ"

    call = vs.frigate_service.get_clip_from_timestamps.call_args
    assert call.args[0] == "cam1"
    assert call.args[1] == pytest.approx(80.0, abs=1e-6)
    assert call.args[2] == pytest.approx(120.0, abs=1e-6)
    assert call.kwargs["download"] is True
