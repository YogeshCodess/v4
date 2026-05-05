"""Tests for tools/datasheet_verify.py — no actual network calls."""
from __future__ import annotations

from unittest.mock import patch

from tools.datasheet_verify import verify_url, verify_urls


def test_empty_url_returns_false():
    assert verify_url("") is False
    assert verify_url(None) is False  # type: ignore[arg-type]


def test_non_string_returns_false():
    assert verify_url(12345) is False  # type: ignore[arg-type]


def test_successful_head_returns_true_for_pdf():
    with patch("tools.datasheet_verify._request", return_value={
        "status": 200, "content_type": "application/pdf; charset=utf-8",
        "final_url": "https://vendor.test/ds.pdf",
    }):
        assert verify_url("https://vendor.test/ds.pdf") is True


def test_successful_head_returns_true_for_html():
    with patch("tools.datasheet_verify._request", return_value={
        "status": 200, "content_type": "text/html",
        "final_url": "https://vendor.test/product",
    }):
        assert verify_url("https://vendor.test/product") is True


def test_network_failure_returns_false():
    with patch("tools.datasheet_verify._request", return_value=None):
        assert verify_url("https://nope.invalid/") is False


def test_non_2xx_returns_false():
    with patch("tools.datasheet_verify._request", return_value={
        "status": 404, "content_type": "text/html", "final_url": "https://x",
    }):
        assert verify_url("https://x") is False


def test_unexpected_content_type_returns_false():
    with patch("tools.datasheet_verify._request", return_value={
        "status": 200, "content_type": "application/json",
        "final_url": "https://x",
    }):
        assert verify_url("https://x") is False


def test_batch_helper():
    with patch("tools.datasheet_verify.verify_url", side_effect=[True, False]):
        out = verify_urls(["https://a", "https://b"])
        assert out == {"https://a": True, "https://b": False}
