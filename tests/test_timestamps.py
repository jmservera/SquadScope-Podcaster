"""Tests for episode timestamp generation and description inclusion (#206)."""

from __future__ import annotations

from podcaster.audio import compute_segment_timeline
from podcaster.config import PodcastConfig
from podcaster.episode import (
    SectionTimestamp,
    build_episode_script,
    compute_section_timestamps,
    format_timestamps_block,
    format_timestamps_html,
    label_script_sections,
    parse_script_segments,
)
from podcaster.publish import inject_timestamps_into_description


class TestSectionTimestamp:
    def test_formatted_zero(self):
        ts = SectionTimestamp(name="Intro", start_seconds=0.0)
        assert ts.formatted == "00:00"

    def test_formatted_seconds_only(self):
        ts = SectionTimestamp(name="Topic", start_seconds=45.7)
        assert ts.formatted == "00:45"

    def test_formatted_minutes_and_seconds(self):
        ts = SectionTimestamp(name="Outro", start_seconds=125.9)
        assert ts.formatted == "02:05"

    def test_str(self):
        ts = SectionTimestamp(name="The Signal", start_seconds=92.0)
        assert str(ts) == "01:32 The Signal"


class TestComputeSegmentTimeline:
    def test_single_segment(self):
        starts, total = compute_segment_timeline([5.0], gap_seconds=0.35)
        assert starts == [0.0]
        assert total == 5.0

    def test_multiple_segments(self):
        starts, total = compute_segment_timeline([3.0, 4.0, 2.0], gap_seconds=0.5)
        assert starts == [0.0, 3.5, 8.0]
        assert total == 10.0

    def test_no_gap(self):
        starts, total = compute_segment_timeline([2.0, 3.0], gap_seconds=0.0)
        assert starts == [0.0, 2.0]
        assert total == 5.0


class TestComputeSectionTimestamps:
    def test_basic_sections(self):
        durations = [2.0, 2.0, 3.0, 3.0, 5.0, 5.0, 2.0, 2.0]
        labels = ["Intro", "Intro", "Topic A", "Topic A", "Topic B", "Topic B", "Outro", "Outro"]
        timestamps = compute_section_timestamps(durations, labels, gap_seconds=0.0)
        assert len(timestamps) == 4
        assert timestamps[0].name == "Intro"
        assert timestamps[0].start_seconds == 0.0
        assert timestamps[1].name == "Topic A"
        assert timestamps[1].start_seconds == 4.0
        assert timestamps[2].name == "Topic B"
        assert timestamps[2].start_seconds == 10.0
        assert timestamps[3].name == "Outro"
        assert timestamps[3].start_seconds == 20.0

    def test_with_gaps(self):
        durations = [3.0, 3.0, 4.0]
        labels = ["Intro", "Main", "Outro"]
        timestamps = compute_section_timestamps(durations, labels, gap_seconds=1.0)
        assert timestamps[0].start_seconds == 0.0
        assert timestamps[1].start_seconds == 4.0  # 3.0 + 1.0 gap
        assert timestamps[2].start_seconds == 8.0  # 4.0 + 3.0 + 1.0 gap

    def test_empty_input(self):
        assert compute_section_timestamps([], [], gap_seconds=0.35) == []

    def test_mismatched_lengths(self):
        durations = [2.0, 3.0, 4.0]
        labels = ["A", "B"]  # shorter than durations
        timestamps = compute_section_timestamps(durations, labels)
        assert len(timestamps) == 2


class TestLabelScriptSections:
    def test_labels_basic_script(self):
        config = PodcastConfig()
        from podcaster.episode import sanitize_article

        article = sanitize_article(
            week="W24",
            title="Test Article",
            url="https://example.com",
            sha256="abc123",
            summary="A summary of test content.",
            beats=[
                {"topic": "First Topic", "points": ["Point A", "Point B"]},
                {"topic": "Second Topic", "points": ["Point C"]},
            ],
        )
        script = build_episode_script(article, config)
        segments = parse_script_segments(script, config)
        labels = label_script_sections(script, segments, config)

        assert len(labels) == len(segments)
        # First 4 should be Intro
        assert labels[0] == "Intro"
        assert labels[1] == "Intro"
        assert labels[2] == "Intro"
        assert labels[3] == "Intro"
        # Last 2 should be Outro
        assert labels[-1] == "Outro"
        assert labels[-2] == "Outro"
        # Middle labels should contain both beat topics
        middle = labels[4:-2]
        assert any("First Topic" in lbl for lbl in middle), (
            f"Expected 'First Topic' in middle labels: {middle}"
        )
        assert any("Second Topic" in lbl for lbl in middle), (
            f"Expected 'Second Topic' in middle labels: {middle}"
        )

    def test_too_few_segments(self):
        labels = label_script_sections("", [("host_a", "hi"), ("host_b", "hey")])
        assert len(labels) == 2


class TestFormatTimestamps:
    def test_format_block(self):
        timestamps = [
            SectionTimestamp(name="Intro", start_seconds=0.0),
            SectionTimestamp(name="Topic A", start_seconds=92.0),
            SectionTimestamp(name="Outro", start_seconds=250.0),
        ]
        block = format_timestamps_block(timestamps)
        assert "00:00 Intro" in block
        assert "01:32 Topic A" in block
        assert "04:10 Outro" in block

    def test_format_html(self):
        timestamps = [
            SectionTimestamp(name="Intro", start_seconds=0.0),
            SectionTimestamp(name="Discussion", start_seconds=60.0),
        ]
        html = format_timestamps_html(timestamps)
        assert "<p>Timestamps:</p>" in html
        assert "00:00 Intro" in html
        assert "01:00 Discussion" in html
        assert "<br/>" in html

    def test_format_html_empty(self):
        assert format_timestamps_html([]) == ""

    def test_format_html_escapes_special_chars(self):
        timestamps = [
            SectionTimestamp(name="<script>alert('xss')</script>", start_seconds=0.0),
            SectionTimestamp(name="Tom & Jerry", start_seconds=60.0),
        ]
        html = format_timestamps_html(timestamps)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "Tom &amp; Jerry" in html


class TestComputeSectionTimestampsWithOffset:
    def test_speech_offset_shifts_all_timestamps(self):
        durations = [3.0, 3.0, 4.0]
        labels = ["Intro", "Main", "Outro"]
        timestamps = compute_section_timestamps(
            durations, labels, gap_seconds=0.0, speech_offset_seconds=10.0
        )
        assert timestamps[0].start_seconds == 10.0
        assert timestamps[1].start_seconds == 13.0
        assert timestamps[2].start_seconds == 16.0


class TestInjectTimestampsIntoDescription:
    def test_appends_timestamps(self):
        desc = "<p>Episode about tech.</p>"
        ts_html = "<p>Timestamps:</p><p>00:00 Intro<br/>01:30 Main</p>"
        result = inject_timestamps_into_description(desc, ts_html)
        assert result == desc + ts_html

    def test_empty_timestamps(self):
        desc = "<p>Some description</p>"
        result = inject_timestamps_into_description(desc, "")
        assert result == desc

    def test_exceeds_limit_drops_timestamps(self):
        desc = "x" * 3980
        ts_html = "<p>Timestamps:</p><p>00:00 Intro</p>"  # 36 chars
        result = inject_timestamps_into_description(desc, ts_html, max_length=4000)
        assert result == desc  # timestamps dropped (3980 + 36 > 4000)

    def test_exactly_at_limit(self):
        desc = "x" * 3960
        ts_html = "y" * 40
        result = inject_timestamps_into_description(desc, ts_html, max_length=4000)
        assert result == desc + ts_html
