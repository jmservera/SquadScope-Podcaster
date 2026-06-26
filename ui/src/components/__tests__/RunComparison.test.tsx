import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { buildComparison, formatDelta, deltaClass, type RunInput } from '../runComparison';

vi.mock('../../api/jobs', () => ({
  fetchJobs: vi.fn(),
  fetchJobDetail: vi.fn(),
  fetchJobProgress: vi.fn(),
  fetchJobProgressSummary: vi.fn(),
}));

import {
  fetchJobs,
  fetchJobDetail,
  fetchJobProgress,
  fetchJobProgressSummary,
} from '../../api/jobs';
import RunComparison from '../RunComparison';
import type { ProgressEvent, StageProgressSummary } from '../../api/jobs';

const mockFetchJobs = vi.mocked(fetchJobs);
const mockFetchJobDetail = vi.mocked(fetchJobDetail);
const mockFetchJobProgress = vi.mocked(fetchJobProgress);
const mockFetchJobProgressSummary = vi.mocked(fetchJobProgressSummary);

function ev(seq: number, stage: string, at: string): ProgressEvent {
  return { seq, stage, at };
}

function summary(stage: string, updatedAt: string, terminal = true): StageProgressSummary {
  return {
    job_id: 'x',
    stage,
    phase: null,
    segment_index: null,
    segment_total: null,
    percent: null,
    message: null,
    updated_at: updatedAt,
    terminal,
    eta: null,
    eta_seconds: null,
  };
}

function run(
  jobId: string,
  createdAt: string,
  events: ProgressEvent[],
  s: StageProgressSummary | null,
  qualityScore: number | null = null
): RunInput {
  return { jobId, title: jobId, createdAt, status: 'published', qualityScore, events, summary: s };
}

const runA = run(
  'job-a',
  '2026-06-25T10:00:00Z',
  [
    ev(1, 'brief', '2026-06-25T10:00:00Z'),
    ev(2, 'synthesis', '2026-06-25T10:00:20Z'),
    ev(3, 'completed', '2026-06-25T10:01:20Z'),
  ],
  summary('completed', '2026-06-25T10:01:20Z'),
  0.9
);

const runB = run(
  'job-b',
  '2026-06-26T10:00:00Z',
  [
    ev(1, 'brief', '2026-06-26T10:00:00Z'),
    ev(2, 'synthesis', '2026-06-26T10:00:30Z'),
    ev(3, 'completed', '2026-06-26T10:02:00Z'),
  ],
  summary('completed', '2026-06-26T10:02:00Z'),
  0.7
);

describe('buildComparison', () => {
  it('orders runs newest first and computes per-stage durations', () => {
    const rows = buildComparison([runA, runB]);
    expect(rows.map((r) => r.jobId)).toEqual(['job-b', 'job-a']);

    const a = rows.find((r) => r.jobId === 'job-a')!;
    expect(a.stageDurations.brief).toBe(20_000); // 10:00:00 → 10:00:20
    expect(a.stageDurations.synthesis).toBe(60_000); // 10:00:20 → 10:01:20
    expect(a.totalDurationMs).toBe(80_000);
  });

  it('computes total delta vs the chronologically previous run', () => {
    const rows = buildComparison([runA, runB]);
    const b = rows.find((r) => r.jobId === 'job-b')!;
    // B total = 120s, A total = 80s → +40s regression
    expect(b.totalDeltaMs).toBe(40_000);
    // A is the oldest → no previous run to diff against
    const a = rows.find((r) => r.jobId === 'job-a')!;
    expect(a.totalDeltaMs).toBeNull();
  });

  it('formats and classifies deltas (faster = improvement)', () => {
    expect(formatDelta(-30_000)).toBe('−30s');
    expect(formatDelta(90_000)).toBe('+1m 30s');
    expect(formatDelta(0)).toBe('');
    expect(deltaClass(-1)).toBe('success-text');
    expect(deltaClass(1)).toBe('error-text');
    expect(deltaClass(null)).toBe('');
  });
});

describe('RunComparison component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchJobs.mockResolvedValue({
      jobs: [
        { job_id: 'job-b', status: 'published', created_at: '2026-06-26', week: null, article_title: 'B' },
        { job_id: 'job-a', status: 'published', created_at: '2026-06-25', week: null, article_title: 'A' },
      ],
      total: 2,
    });
    mockFetchJobDetail.mockImplementation((id: string) =>
      Promise.resolve({
        job_id: id,
        status: 'published',
        created_at: id === 'job-b' ? '2026-06-26T10:00:00Z' : '2026-06-25T10:00:00Z',
        expires_at: null,
        week: null,
        article_url: null,
        article_title: id.toUpperCase(),
        generation: null,
        publishing: null,
        lifecycle: null,
        quality_score: id === 'job-b' ? 0.7 : 0.9,
        warnings: null,
      })
    );
    mockFetchJobProgress.mockImplementation((id: string) =>
      Promise.resolve({
        job_id: id,
        current: null,
        events: id === 'job-b' ? runB.events : runA.events,
        last_seq: 3,
        terminal: true,
      })
    );
    mockFetchJobProgressSummary.mockImplementation((id: string) =>
      Promise.resolve(id === 'job-b' ? runB.summary! : runA.summary!)
    );
  });

  it('renders a comparison table with stage rows for each run', async () => {
    render(<RunComparison />);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Run Comparison' })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText('JOB-B')).toBeInTheDocument();
    });
    expect(screen.getByText('JOB-A')).toBeInTheDocument();
    expect(screen.getByRole('rowheader', { name: 'Total' })).toBeInTheDocument();
    expect(screen.getByRole('rowheader', { name: 'Quality' })).toBeInTheDocument();
    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('70%')).toBeInTheDocument();
  });

  it('reloads when the run count selector changes', async () => {
    render(<RunComparison />);
    await waitFor(() => expect(mockFetchJobs).toHaveBeenCalledWith(5, 0));
    await userEvent.selectOptions(
      screen.getByLabelText('Number of runs to compare'),
      '8'
    );
    await waitFor(() => expect(mockFetchJobs).toHaveBeenCalledWith(8, 0));
  });
});
