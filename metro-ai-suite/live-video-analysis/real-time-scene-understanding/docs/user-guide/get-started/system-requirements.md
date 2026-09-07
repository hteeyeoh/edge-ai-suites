# System Requirements

This page summarizes the recommended environment for running Real Time Scene Understanding.

## Hardware Platforms used for validation

- This application is specifically targeting Intel&reg; Core&trade; platforms. Intel&reg; Core&trade; Ultra 2 and 3 with integrated GPU are currently supported.
- Intel&reg; Core&trade; Ultra platforms with integrated NPU are supported for LLM inference offloading.
- While there is no hard restriction in using this application on Intel&reg; Xeon&reg; platforms with or without Intel&reg; Arc&trade; GPUs, users are requested to raise a feature ticket in case of any requirement.

## Operating Systems used for validation

- Ubuntu: Refer to the official [documentation](https://dgpu-docs.intel.com/devices/hardware-table.html) for details on required kernel version. For the listed hardware platforms, the kernel requirement translates to Ubuntu 24.04 or Ubuntu 24.10 depending on the GPU used.

## Minimum Requirements

| **Component**  | **Minimum** | **Recommended** |
|----------------|-------------|-----------------|
| **Memory**     | 16 GB       | 32 GB           |
| **Disk Space** | 64 GB SSD   | 128 GB SSD      |

## Software Requirements

- Docker Engine and Docker Compose
- Intel&reg; Graphics compute runtime (if using Intel GPU for inference acceleration)
- RTSP source reachable from the `real-time-scene-understanding` container (optional, can be added via UI)

## Network / Ports

Default ports (configurable via environment variables):

- `PORT=9100` (Dashboard UI and REST API)
- `METRICS_PORT=9090` (Live metrics WebSocket service)

## Model Requirements

The application requires two VLM models. One model is used by the alert-filtering pipeline for frame-level alert gating, and the other is used by the multi-frame deep-analyzer pipeline to analyze video tensors from segmented clips. The deep-analyzer model must support video modality, and compatibility is model-specific. The Qwen3.5 model family has been validated for this application.

Configure model selection via `.env` file as below:

- ALERT_VLM_MODEL=Qwen3.5-0.8B
- DEEP_ANALYZER_MODEL=Qwen3.5-2B

The application can use pre-converted OpenVINO models. Example model options:

- `OpenVINO/Qwen3.5-0.8B-int4-ov`
- `OpenVINO/Qwen3.5-2B-int4-ov`

Model files can also be prepared using the provided scripts and model directories in this repository (for example, `model_download_scripts/download_models.sh` and `ov_models/`). Pleasae refer to [Model Preparion Guide](./model-preparation.md) for the steps.

> **Note:** Use pre-converted OpenVINO IR VLM models from the
> [OpenVINO organization on Hugging Face](https://huggingface.co/OpenVINO)
> for best compatibility with OVMS. These models are already optimized and
> require no additional conversion. Browse the available models at
> [OpenVINO VLM Models](https://huggingface.co/collections/OpenVINO/visual-language-models) and select the variant that matches
> your target device and quantization requirements.

## Validation

Proceed to [Get Started](../get-started.md) once Docker is installed and internet connectivity is available for model downloads.
