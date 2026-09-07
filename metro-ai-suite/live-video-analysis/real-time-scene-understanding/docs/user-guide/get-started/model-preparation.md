# Model Preparation

Real Time Scene Understanding requires at least one Vision Language Model (VLM) under `ov_models/`.

The provided helper uses the ephemeral model-download container flow from the [Model Download project](https://docs.openedgeplatform.intel.com/dev/edge-ai-libraries/model-download/index.html) in Open Edge Platform. It starts a temporary container, downloads or converts the model, writes the files to this repository, and removes the container when finished. No separate model-download setup is required.

## Prerequisites

- Docker is installed and running.
- `curl` and `python3` are available on the host.
- Commands are run from the `real-time-scene-understanding` directory.
- For gated Hugging Face models, set a token first:

	```bash
	export HUGGINGFACEHUB_API_TOKEN=<your-huggingface-token>
	```

## Usage

Use the helper script with the following arguments:

```bash
./model_download_scripts/download_models.sh \
	--model <huggingface-model-id> \
	--type vlm \
	--weight-format <int4|int8|fp16> \
	--device <CPU|GPU|NPU>
```

**Parameters:**
- `--model`: Hugging Face model identifier (for example, `OpenGVLab/InternVL2-1B`).
- `--type`: Model category. Use `vlm` for Vision Language Models.
- `--weight-format`: Precision/quantization format. Supported values are `int4`, `int8`, and `fp16`.
- `--device`: Target conversion device (for example, `CPU`, `GPU`, or `NPU`, depending on host support).

**Weight format options:**

Supported weight formats are `int4`, `int8`, and `fp16`. The default is `int8`.

| Format | Memory use | Accuracy | When to use |
|--------|-----------|----------|-------------|
| `int4` | Lowest | Lower | Memory-constrained systems |
| `int8` | Medium | Good | Recommended default |
| `fp16` | Highest | Best | Maximum accuracy, more RAM required |

## Download a VLM Model

Use the following commands to run conversion for the desired target device. Generated models are stored under `ov_models/`.

- For CPU:

	```bash
	./model_download_scripts/download_models.sh \
		--model Qwen/Qwen3.5-0.8B \
		--type vlm \
		--weight-format int4 \
		--device CPU
	```

- For GPU:

	```bash
	./model_download_scripts/download_models.sh \
		--model Qwen/Qwen3.5-0.8B \
		--type vlm \
		--weight-format int4 \
		--device GPU
	```

- For NPU, use `int4` quantization:

	```bash
	./model_download_scripts/download_models.sh \
		--model OpenGVLab/InternVL2-1B \
		--type vlm \
		--weight-format int4 \
		--device NPU
	```

	> Note: NPU currently requires `int4` quantization for VLM conversion. If you pass `--device NPU` with `int8` or `fp16`, the script automatically overrides it to `int4`.
	> Note: Not all models are supported on NPU. See the [OpenVINO Model Support Page](https://docs.openvino.ai/2026/documentation/compatibility-and-support/supported-models.html) for model and device compatibility details.

You can also download and convert for multiple target devices in a single command by passing a comma-separated `--device` list:

```bash
./model_download_scripts/download_models.sh \
	--model Qwen/Qwen3.5-0.8B \
	--type vlm \
	--weight-format int4 \
	--device CPU,GPU
```

Downloaded VLM models are stored under per-device directories in `ov_models/`.

Each VLM output directory is placed under its target device path so the UI can automatically associate models with the selected VLM device.

Example:

| `--device` flag | Example output directory | VLM device tag |
|---|---|---|
| `CPU` (or omitted) | `ov_models/cpu/InternVL2-1B` | `CPU` |
| `GPU` | `ov_models/gpu/InternVL2-1B` | `GPU` |
| `NPU` | `ov_models/npu/InternVL2-1B` | `NPU` |

### VLM Models Validated

The following VLM models are validated:

| Model Name | Supported Hardware Devices | OVMS Release TAG Version |
| --- | --- | --- |
| OpenGVLab/InternVL2-1B | CPU, GPU, NPU | v2026.1 |
| Qwen/Qwen3.5-0.8B | CPU, GPU | v2026.1 |
| Qwen/Qwen3.5-2B | CPU, GPU | v2026.1 |

> Note: `OVMS_RELEASE_TAG` in `.env` controls the OVMS image version used by the model download/conversion flow. Refer to the validated-model table above, or consult official OpenVINO documentation for supported models and corresponding OVMS versions. Using a different tag can change the bundled `transformers`/OpenVINO toolchain and may cause conversion failures.
>
> Note: If you want to use newer Hugging Face models, you may need a newer OVMS/OpenVINO stack for conversion, which means updating `OVMS_RELEASE_TAG`.

As of the time of writing, the latest stable DL Streamer release is `2026.1.0`, built on top of OpenVINO `v2026.1`.

## Troubleshooting

- If Docker cannot pull `intel/model-download:<tag>`, check the `MODEL_DOWNLOAD_IMAGE_TAG` value in `.env` (defaults to `latest`; this is independent of the application image `TAG`).
- If a gated model fails with an authentication error, set `HUGGINGFACEHUB_API_TOKEN` and rerun the command.
- If a download process is interrupted or fails due to network issues, remove the `ovms_model` folder and the model-specific folder from the failed run under `ov_models/`. Then rerun the command. The ephemeral model-download container is automatically cleaned up when the helper exits.
