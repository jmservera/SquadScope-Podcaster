import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import StageTimeline from '../StageTimeline';
import { buildStageRows, stageStatusBadge, PIPELINE_STAGES } from '../stageTimeline';
import type { ProgressEvent, StageProgressSummary } from '../../api/jobs';

function ev(seq: number, stage: string, at: string): ProgressEvent {
  return { seq, stage, at };
}

function summary(partial: Partial<StageProgressSummary>): StageProgressSummary {
  return {
    job_id: 'job-1',
    stage: null,
    phase: 'pending',
    segment_index: null,
    segment_total: null,
    percent: null,
    message: null,
    updated_at: null,
    terminal: false,
    eta: null,
    eta_seconds: null,
    ...partial,
  };
}

describe('buildStageRows', () => {
  it('returns one row per canonical pipeline stage', () => {
    const rows = buildStageRows([], null);
    expect(rows.map((r) => r.stage)).toEqual(PIPELINE_STAGES.map((s) => s.stage));
    expect(rows.every((r) => r.status === 'pending')).toBe(true);
  });

  it('computes durations for completed stages and marks current in-progress', () => {
    const events = [
      ev(1, 'brief', '2026-06-26T12:00:00Z'),
      ev(2, 'script', '2026-06-26T12:00:30Z'),
      ev(3, 'synthesis', '2026-06-26T12:01:00Z'),
    ];
    const s = summary({ stage: 'synthesis', updated_at: '2026-06-26T12:02:00Z' });
    const rows = buildStageRows(events, s);
    const byStage = Object.fromEntries(rows.map((r) => [r.stage, r]));

    expect(byStage.brief.status).toBe('completed');
    expect(byStage.brief.durationMs).toBe(30_000);
    expect(byStage.script.status).toBe('completed');
    expect(byStage.script.durationMs).toBe(30_000);
    expect(byStage.synthesis.status).toBe('in_progress');
    expect(byStage.synthesis.durationMs).toBe(60_000);
    expect(byStage.compose.status).toBe('pending');
    expect(byStage.mux.status).toBe('pending');
  });

  it('marks all working stages completed when the job finished', () => {
    const events = [
      ev(1, 'brief', '2026-06-26T12:00:00Z'),
      ev(2, 'script', '2026-06-26T12:00:30Z'),
      ev(3, 'synthesis', '2026-06-26T12:01:00Z'),
      ev(4, 'compose', '2026-06-26T12:02:00Z'),
      ev(5, 'mux', '2026-06-26T12:03:00Z'),
      ev(6, 'publish', '2026-06-26T12:03:30Z'),
      ev(7, 'completed', '2026-06-26T12:04:00Z'),
    ];
    const s = summary({ stage: 'completed', terminal: true, updated_at: '2026-06-26T12:04:00Z' });
    const rows = buildStageRows(events, s);
    expect(rows.every((r) => r.status === 'completed')).toBe(true);
    const publish = rows.find((r) => r.stage === 'publish')!;
    expect(publish.durationMs).toBe(30_000);
  });

  it('attributes failure to the last working stage', () => {
    const events = [
      ev(1, 'brief', '2026-06-26T12:00:00Z'),
      ev(2, 'synthesis', '2026-06-26T12:01:00Z'),
      ev(3, 'failed', '2026-06-26T12:01:30Z'),
    ];
    const s = summary({ stage: 'failed', terminal: true, updated_at: '2026-06-26T12:01:30Z' });
    const rows = buildStageRows(events, s);
    const byStage = Object.fromEntries(rows.map((r) => [r.stage, r]));
    expect(byStage.brief.status).toBe('completed');
    expect(byStage.synthesis.status).toBe('failed');
    expect(byStage.compose.status).toBe('pending');
  });

  it('marks earlier unreached stages as skipped', () => {
    const events = [ev(1, 'compose', '2026-06-26T12:00:00Z')];
    const s = summary({ stage: 'compose', updated_at: '2026-06-26T12:00:30Z' });
    const rows = buildStageRows(events, s);
    const byStage = Object.fromEntries(rows.map((r) => [r.stage, r]));
    expect(byStage.brief.status).toBe('skipped');
    expect(byStage.script.status).toBe('skipped');
    expect(byStage.synthesis.status).toBe('skipped');
    expect(byStage.compose.status).toBe('in_progress');
    expect(byStage.publish.status).toBe('pending');
  });

  it('does not mark uninstrumented later stages as skipped on a terminal job', () => {
    // Only the brief stage emitted an event before the job reached a terminal
    // state (e.g. later progress events aged out of the retained window). The
    // un-observed later stages must stay neutral/"pending", not "skipped",
    // since the data does not prove they were skipped (#474 review).
    const events = [ev(1, 'brief', '2026-06-26T12:00:00Z')];
    const s = summary({ stage: 'completed', terminal: true, updated_at: '2026-06-26T12:05:00Z' });
    const rows = buildStageRows(events, s);
    const byStage = Object.fromEntries(rows.map((r) => [r.stage, r]));
    expect(byStage.brief.status).toBe('completed');
    expect(byStage.script.status).toBe('pending');
    expect(byStage.synthesis.status).toBe('pending');
    expect(byStage.publish.status).toBe('pending');
    expect(rows.some((r) => r.status === 'skipped')).toBe(false);
  });

  it('maps skipped to a neutral badge matching the grey bar', () => {
    expect(stageStatusBadge('skipped')).toBe('badge-muted');
    expect(stageStatusBadge('completed')).toBe('badge-success');
    expect(stageStatusBadge('failed')).toBe('badge-error');
  });
});

describe('StageTimeline component', () => {
  it('shows an empty message when there is no progress', () => {
    render(<StageTimeline events={[]} summary={null} />);
    expect(screen.getByText('No stage progress recorded yet.')).toBeInTheDocument();
  });

  it('renders a row per stage with status badges', () => {
    const events = [
      ev(1, 'brief', '2026-06-26T12:00:00Z'),
      ev(2, 'synthesis', '2026-06-26T12:01:00Z'),
    ];
    const s = summary({ stage: 'synthesis', updated_at: '2026-06-26T12:02:00Z' });
    render(<StageTimeline events={events} summary={s} now={Date.parse('2026-06-26T12:02:00Z')} />);

    expect(screen.getByRole('list', { name: 'Pipeline stage timeline' })).toBeInTheDocument();
    expect(screen.getByLabelText('Record: In progress')).toBeInTheDocument();
    expect(screen.getByLabelText('Brief: Completed')).toBeInTheDocument();
    expect(screen.getByLabelText('Publish: Pending')).toBeInTheDocument();
  });
});
