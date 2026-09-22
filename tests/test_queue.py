"""Queue authority timestamp regression tests."""

from datetime import datetime, timezone

from podcaster.queue import _parse_messages


def test_rest_message_carries_authoritative_next_visible_time():
    messages = _parse_messages(
        b"""<?xml version="1.0" encoding="utf-8"?>
<QueueMessagesList>
  <QueueMessage>
    <MessageId>m1</MessageId>
    <PopReceipt>r1</PopReceipt>
    <MessageText>body</MessageText>
    <DequeueCount>2</DequeueCount>
    <NextVisibleTime>Tue, 22 Sep 2026 18:55:00 GMT</NextVisibleTime>
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
    <NextVisibleTime>not-a-timestamp</NextVisibleTime>
  </QueueMessage>
</QueueMessagesList>"""
    )

    assert messages[0].next_visible_on is None
