# Dev-only Benchmark Harness

This directory contains a benchmark harness that is intentionally kept outside the production app runtime.

## Purpose

The benchmark is designed to answer four questions for a dev or customer-sizing workflow:

- how many VLM frames per stream the hardware can sustain
- how many deep-analysis segments per stream can run before the machine is overloaded
- which model/device combinations provide the best throughput and latency
- what the safe operating envelope is for a target deployment

## Important

This is not part of the production deployment path and should not be wired into the app startup or API routes.

## Run the benchmark against a live app

1. Start the app locally on the expected port.

	```bash
	docker compose up -d --build
	```

	or, if you are running the Python app directly:

	```bash
	python3 app/main.py
	```

2. Run the benchmark from the project root and point it at the live app endpoint:

	```bash
	python3 benchmarks/benchmark_runner.py \
		--run-id dev-live \
		--api-base-url http://localhost:9100 \
		--duration-seconds 180 \
		--warmup-seconds 30 \
		--sample-interval-seconds 5
	```

3. The harness waits for the warmup period, samples the live `/streams`, `/streams/{stream_id}/deep-metrics`, and resource metrics endpoints at the configured interval, and writes CSV output after the measurement window completes.

The live path reports the model and device selected by the running app through `/runtime-config.js`. The current app names are `alertVlmModel`, `alertVlmDevice`, `deepAnalyzerModel`, and `deepAnalyzerDevice`. It does not change a running OpenVINO pipeline. To compare live model/device configurations, restart the app between runs with different `ALERT_VLM_MODEL`, `ALERT_VLM_DEVICE`, `DEEP_ANALYZER_MODEL`, and `DEEP_ANALYZER_DEVICE` values, using a unique `--run-id` for each run.

## Generate the summary

The benchmark writes a raw per-sample CSV:

```bash
python3 benchmarks/benchmark_runner.py \
	--run-id sample-run \
	--api-base-url http://localhost:9100 \
	--duration-seconds 180 \
	--warmup-seconds 30 \
	--sample-interval-seconds 5
```

This writes one raw CSV:

- results/raw/raw_benchmark_<run-id>.csv

The `run-id` is included in the filename so a new benchmark run does not overwrite an earlier report. Characters that are unsafe in filenames are replaced with `_`.

Each row is one pipeline observation from one stream sampling interval. Each stream sample produces two rows: one with `metric_source=alert_vlm` and one with `metric_source=deep_analyzer`. No averages, percentiles, maxima, safe-capacity flags, or summary rows are generated. Filter the CSV on `metric_source` to view only one pipeline.

The app endpoint `GET /streams/{stream_id}/deep-metrics` reports the latest completed deep-analysis result and the number of completed segments. The benchmark writes alert VLM values with `alert_vlm_` prefixes and deep-analyzer values with `deep_` prefixes so the two inference pipelines are not confused.

`--warmup-seconds` allows the models and streams to stabilize before sampling. `--duration-seconds` controls the measurement window. `--sample-interval-seconds` controls how often the runner polls the live app; lower values provide more samples but create more endpoint and metrics traffic.

If the app is unreachable or has no active streams, the command fails instead of creating synthetic benchmark data. This prevents test values from being mistaken for live hardware measurements.

## Validate the summary logic

Run the unit checks before or after benchmarking to confirm live resource parsing stays stable:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Output fields

The CSV contains:

- `alert_vlm_ttft_ms`, `alert_vlm_tpot_ms`, `alert_vlm_throughput_tps`, `alert_vlm_total_tokens_generated`
- `deep_ttft_ms`, `deep_tpot_ms`, `deep_throughput_tps`, `deep_total_duration_ms`, `deep_frames_sampled`, `deep_total_tokens_generated`
- `deep_segments_submitted`, `deep_segments_queued`, `deep_segments_in_flight`, `deep_segments_active`, `deep_segments_completed`, `deep_segments_persisted`, `deep_segments_failed`, `deep_segments_max_in_flight`
- `deep_segments_processed_10s`, `deep_segments_per_second_10s`
- `metric_source` with values `alert_vlm` or `deep_analyzer`

`deep_segments_processed_10s` is the number of newly completed deep-analysis jobs since the previous non-overlapping 10-second window for that stream. It is populated at the end of each 10-second window; earlier samples are blank. `deep_segments_queued` includes jobs waiting for dispatch, while `deep_segments_in_flight` is the number currently executing. The current implementation has one dispatcher, so in-flight work is normally at most one.
- CPU/RAM/GPU/NPU utilization

## Notes

This benchmark harness is intended for development and live hardware benchmarking only. It is not a production telemetry or deployment path.
