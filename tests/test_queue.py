"""Queue authority timestamp regression tests."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from podcaster.queue import (
    AzureStorageQueueBackend,
    ConnectionStringQueueBackend,
    QueueMessage,
    _parse_messages,
)


def test_rest_message_carries_authoritative_next_visible_time():
    messages = _parse_messages(
        b"""<?xml version="1.0" encoding="utf-8"?>
<QueueMessagesList>
  <QueueMessage>
    <MessageId>m1</MessageId>
    <PopReceipt>r1</PopReceipt>
    <MessageText>body</MessageText>
    <DequeueCount>2</DequeueCount>
    <TimeNextVisible>Tue, 22 Sep 2026 18:55:00 GMT</TimeNextVisible>
  </QueueMessage>
</QueueMessagesList>"""
    )

    assert messages[0].next_visible_on == datetime(2026, 9, 22, 18, 55, tzinfo=timezone.utc)


def test_invalid_rest_next_visible_time_fails_closed():
    messages = _parse_messages(
        b"""<QueueMessagesList>
  <QueueMessage>
    <MessageId>m1</MessageId>
    <PopReceipt>r1</PopReceipt>
    <MessageText>body</MessageText>
    <DequeueCount>1</DequeueCount>
    <TimeNextVisible>not-a-timestamp</TimeNextVisible>
  </QueueMessage>
</QueueMessagesList>"""
    )

    assert messages[0].next_visible_on is None


@pytest.mark.parametrize(
    "response,accepted",
    [
        (
            b"<QueueMessage><PopReceipt>r2</PopReceipt>"
            b"<TimeNextVisible>Tue, 22 Sep 2026 18:55:00 GMT</TimeNextVisible></QueueMessage>",
            True,
        ),
        (b"<QueueMessage><PopReceipt>r2</PopReceipt></QueueMessage>", False),
    ],
)
def test_rest_visibility_renewal_uses_rotated_receipt_and_requires_server_expiry(
    monkeypatch, response, accepted
):
    queue = AzureStorageQueueBackend(
        "https://example.queue.core.windows.net",
        "video-jobs",
        credential=MagicMock(get_token=lambda scope: "redacted"),
    )
    request_seen = []

    def open_request(request, *, timeout):
        request_seen.append(request)
        handle = MagicMock()
        handle.__enter__.return_value.read.return_value = response
        return handle

    monkeypatch.setattr("podcaster.queue.urlopen", open_request)
    original = QueueMessage("message-id", "r1", "body", 1)
    if accepted:
        renewed = queue.renew_message(original, visibility_timeout=240)
        assert renewed.pop_receipt == "r2"
        assert renewed.next_visible_on == datetime(2026, 9, 22, 18, 55, tzinfo=timezone.utc)
    else:
        with pytest.raises(ValueError, match="authority"):
            queue.renew_message(original, visibility_timeout=240)
    assert request_seen[0].get_method() == "PUT"
    assert "visibilitytimeout=240" in request_seen[0].full_url
    assert "popreceipt=r1" in request_seen[0].full_url


def test_sdk_visibility_renewal_uses_object_response():
    queue = ConnectionStringQueueBackend.__new__(ConnectionStringQueueBackend)
    queue._client = MagicMock()
    queue._client.update_message.return_value = MagicMock(
        pop_receipt="r2",
        next_visible_on=datetime(2026, 9, 22, 18, 55, tzinfo=timezone.utc),
    )
    message = QueueMessage("m1", "r1", "body", 1)
    renewed = queue.renew_message(message, visibility_timeout=240)
    queue._client.update_message.assert_called_once_with(
        "m1", "r1", content="body", visibility_timeout=240
    )
    assert renewed.pop_receipt == "r2"
    assert renewed.next_visible_on == datetime(2026, 9, 22, 18, 55, tzinfo=timezone.utc)
