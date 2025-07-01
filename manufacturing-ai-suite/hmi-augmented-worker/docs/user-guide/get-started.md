# Get Started

The `get started` guide explains how the HMI Augmented Worker application can be setup on a Type-2 hypervisor with the HMI on a Windows VM while deploying the RAG pipeline natively on the Hypervisor host (EMT).

## Prerequisites

Installing the Windows side of the application is covered in detail in this documentation. The sample application has mandatory prerequisites that are covered in other documentation. The user is required to refer to the respective documentation for the details. The prerequisites listed below cover such dependencies.

- Set up EMT based Type-2 Hypervisor host on target hardware. Refer to [EMT as Hypervisor](). EMT is a reference hypervisor which has been used for validation. Other Type-2 hypervisors can also be used as per user preference.

- The sample application utilizes [Chat Question and Answer Core](https://github.com/open-edge-platform/edge-ai-libraries/tree/main/sample-applications/chat-question-and-answer-core) for the RAG pipeline. The documentation available with `chat question and answer core` covers the details of how to set it up and get it to run. The documentation here will more focus on how this application can be adapted and integrated in the context of HMI application.

- Support hardware details are available in the [system requirements](./system-requirements.md) page.

## Application Configuration

This section covers the details of the configuration of each constitutent blocks of the application.

### Chat-Question-and-Answer Core (ChatQnA Core)

Following knobs are available for configuration of the ChatQnA Core application.

1. **Number of CPUs allocation**: Allocation of cores to the VM (or host) running the ChatQnA Core is configurable in combination with the core requirements for the Windows VM. Together with the model selection, the core selection forms a tradeoff of performance vs. accuracy which need to be accounted for. Just like the CPU, it is also possible to share the GPU (iGPU or dGPU) between the Windows VM and applications running on host through SRIOV. However, this has not been validated in the current version of the sample application.

2. **Deployment Option**: It is possible to deploy ChatQnA core both using Helm or with docker compose. The former will need Kubevirt based infrastructure which can manage both the VM(s) and the containers.

3. **Model configuration**: Model used, especially its parameter count, not only determines the accuracy but also the required compute. As explained in (1), this forms a performance - accuracy tradeoff which needs to be factored in when selecting the model. Model here maps to the requirement of the entire RAG pipeline, viz. LLM, Embedding, Retriever, and Reranker.

### File Watcher Service

The File Watcher service will need to be built and deployed. There is no pre-built option provided currently. It runs in the Windows VM and is responsible for watching the configured folder for new contents. The new contents are added to the RAG context database which is then used by the RAG pipeline when responding to user queries. Refer to [Build File Watcher Service from Source](./how-to-build-from-source.md#build-file-watcher-service-from-source) to compile the file watcher service executable binary from source.

In addition to the File Watcher service, the WebUI interface to access the RAG functionality is also run on the Windows VM. This is explained in the [how to use the application](#how-to-use-the-application) section.

## How to use the application?

_Todo: List the steps to use the application_

## Advanced Setup

- [How to Build from Source and Deploy](./how-to-build-from-source.md): Guide to build the sample application services from source and docker compose deployment