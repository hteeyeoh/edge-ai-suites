# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from pydantic import BaseModel
from typing import Dict


class Rule(BaseModel):
    id: str
    label: str
    action: str
    camera: str | None = None
    source: str | None = None
    count: int | None = None
    region_id: str | None = None
    region_name: str | None = None
    scene_id: str | None = None
    region_thresholds: Dict[str, int] | None = None
