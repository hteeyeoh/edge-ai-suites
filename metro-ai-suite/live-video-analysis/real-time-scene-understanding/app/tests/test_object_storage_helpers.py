# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for static helpers in backend.services.object_storage.SeaweedFSStorage.

These are exercised via the class directly (no instantiation) since
SeaweedFSStorage.__init__ creates a real boto3 client and touches the bucket.
"""

from __future__ import annotations

import pytest
from backend.services import object_storage as object_storage_module
from backend.services.object_storage import SeaweedFSStorage


class TestBackoffDelay:
    def test_scales_linearly_with_attempt(self, monkeypatch):
        monkeypatch.setattr(object_storage_module.settings, "SEAWEEDFS_RETRY_DELAY_SECONDS", 1.0)
        monkeypatch.setattr(object_storage_module.settings, "SEAWEEDFS_MAX_RETRY_DELAY_SECONDS", 10.0)

        assert SeaweedFSStorage._backoff_delay(1) == 1.0
        assert SeaweedFSStorage._backoff_delay(3) == 3.0

    def test_caps_at_configured_maximum(self, monkeypatch):
        monkeypatch.setattr(object_storage_module.settings, "SEAWEEDFS_RETRY_DELAY_SECONDS", 1.0)
        monkeypatch.setattr(object_storage_module.settings, "SEAWEEDFS_MAX_RETRY_DELAY_SECONDS", 2.0)

        assert SeaweedFSStorage._backoff_delay(10) == 2.0


class TestToS3MetadataValue:
    def test_plain_string_is_unchanged(self):
        assert SeaweedFSStorage._to_s3_metadata_value("hello world") == "hello world"

    def test_non_string_is_stringified(self):
        assert SeaweedFSStorage._to_s3_metadata_value(42) == "42"

    @pytest.mark.parametrize("bad_char", ["\n", "\r", "\t"])
    def test_normalizes_whitespace_control_characters(self, bad_char):
        value = f"line one{bad_char}line two"
        assert SeaweedFSStorage._to_s3_metadata_value(value) == "line one line two"


class TestIsBucketMissingError:
    def test_true_for_404_status_code(self):
        exc = Exception("boom")
        exc.response = {"ResponseMetadata": {"HTTPStatusCode": 404}, "Error": {}}
        assert SeaweedFSStorage._is_bucket_missing_error(exc) is True

    @pytest.mark.parametrize("code", ["404", "NoSuchBucket", "NotFound"])
    def test_true_for_known_error_codes(self, code):
        exc = Exception("boom")
        exc.response = {"ResponseMetadata": {"HTTPStatusCode": 400}, "Error": {"Code": code}}
        assert SeaweedFSStorage._is_bucket_missing_error(exc) is True

    def test_false_when_no_response_attribute(self):
        assert SeaweedFSStorage._is_bucket_missing_error(Exception("boom")) is False

    def test_false_for_unrelated_error(self):
        exc = Exception("boom")
        exc.response = {"ResponseMetadata": {"HTTPStatusCode": 500}, "Error": {"Code": "InternalError"}}
        assert SeaweedFSStorage._is_bucket_missing_error(exc) is False
