# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from pydantic import BaseModel, Field, model_validator


class Broker(BaseModel):
    id: str
    name: str
    host: str
    port: int = 1883
    # A broker subscribes to one connection per topic set here (camera and/or region).
    camera_topic: str | None = None
    region_topic: str | None = None
    type: str = "scenescape"
    use_tls: bool = True
    throttle_interval: float = 2.0
    enabled: bool = True
    # Consumed by setup.sh to build the Frigate camera list; unused at runtime.
    rtsp_host: str | None = None
    rtsp_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def _require_at_least_one_topic(self) -> "Broker":
        if not (self.camera_topic or self.region_topic):
            raise ValueError("Broker must set at least one of: camera_topic, region_topic")
        return self

    @property
    def topics(self) -> list[str]:
        """All MQTT topic filters this broker subscribes to, in a stable order."""
        return [t for t in (self.camera_topic, self.region_topic) if t]
