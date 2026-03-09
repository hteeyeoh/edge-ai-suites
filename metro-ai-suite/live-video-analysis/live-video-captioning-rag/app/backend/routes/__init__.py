# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# API Route handler
from .chat import router as chat_router
from .model import router as model_router
from .health import router as health_router

__all__ = [
    "chat_router",
    "model_router",
    "health_router"
]