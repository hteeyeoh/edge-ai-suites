import unittest
import csv
import tempfile
from pathlib import Path

from benchmarks.benchmark_runner import _add_deep_window_metrics
from benchmarks.benchmark_runner import _aggregate_live_resource_metrics
from benchmarks.benchmark_runner import _runtime_model_config
from benchmarks.csv_export import RAW_FIELDNAMES, write_raw_csv


class BenchmarkLiveTest(unittest.TestCase):
    def test_aggregate_live_resource_metrics(self):
        payload = {
            "metrics": [
                {"name": "cpu_usage_user", "value": 42.0, "labels": {"cpu": "cpu-total"}},
                {"name": "mem_used_percent", "value": 61.0, "labels": {}},
                {"name": "gpu_engine_usage_usage", "value": 72.0, "labels": {"engine": "render/3d0"}},
                {"name": "npu_utilization", "value": 90.0, "labels": {}},
            ]
        }

        resources = _aggregate_live_resource_metrics(payload)

        self.assertEqual(resources["cpu"], 42.0)
        self.assertEqual(resources["ram"], 61.0)
        self.assertEqual(resources["gpu"], 72.0)
        self.assertEqual(resources["npu"], 90.0)

    def test_report_contains_alert_vlm_and_deep_model_device_fields(self):
        row = {
            "stream_id": "stream-1",
            "metric_source": "alert_vlm",
            "alert_vlm_model": "InternVL2-1B",
            "alert_vlm_device": "CPU",
            "alert_vlm_total_tokens_generated": 12,
            "deep_analyzer_model": "Qwen3.5-2B-int4-ov",
            "deep_analyzer_device": "GPU",
            "deep_total_tokens_generated": 24,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "raw.csv"
            write_raw_csv(output_path, [row])
            with output_path.open(encoding="utf-8", newline="") as report_file:
                report = csv.DictReader(report_file)
                exported = next(report)

        self.assertEqual(
            [
                "alert_vlm_model",
                "alert_vlm_device",
                "deep_analyzer_model",
                "deep_analyzer_device",
            ],
            [field for field in RAW_FIELDNAMES if "model" in field or "device" in field],
        )
        self.assertEqual(exported["alert_vlm_model"], "InternVL2-1B")
        self.assertEqual(exported["metric_source"], "alert_vlm")
        self.assertEqual(exported["alert_vlm_device"], "CPU")
        self.assertEqual(exported["deep_analyzer_model"], "Qwen3.5-2B-int4-ov")
        self.assertEqual(exported["deep_analyzer_device"], "GPU")
        self.assertEqual(exported["alert_vlm_total_tokens_generated"], "12")
        self.assertEqual(exported["deep_total_tokens_generated"], "24")

    def test_deep_segments_processed_over_ten_seconds(self):
        baselines = {"stream-1": (100.0, 12)}
        rows = [
            {
                "metric_source": "deep_analyzer",
                "stream_id": "stream-1",
                "deep_segments_completed": 15,
            }
        ]

        _add_deep_window_metrics(rows, baselines, now=110.0)

        self.assertEqual(rows[0]["deep_segments_processed_10s"], 3)
        self.assertEqual(rows[0]["deep_segments_per_second_10s"], 0.3)

    def test_runtime_config_uses_alert_vlm_names(self):
        model, device, deep_model, deep_device = _runtime_model_config(
            {
                "alertVlmModel": "Qwen3.5-0.8B-int4-ov",
                "alertVlmDevice": "CPU",
                "deepAnalyzerModel": "Qwen3.5-2B-int4-ov",
                "deepAnalyzerDevice": "CPU",
            }
        )

        self.assertEqual(model, "Qwen3.5-0.8B-int4-ov")
        self.assertEqual(device, "CPU")
        self.assertEqual(deep_model, "Qwen3.5-2B-int4-ov")
        self.assertEqual(deep_device, "CPU")


if __name__ == "__main__":
    unittest.main()
