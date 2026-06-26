import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../api/jobs', () => ({
  fetchJobs: vi.fn(),
  fetchJobDetail: vi.fn(),
  fetchJobLogs: vi.fn(),
}));

import { fetchJobs, fetchJobDetail, fetchJobLogs, type LogEntry } from '../../api/jobs';
import JobMonitor from '../JobMonitor';

const mockFetchJobs = vi.mocked(fetchJobs);
const mockFetchJobDetail = vi.mocked(fetchJobDetail);
const mockFetchJobLogs = vi.mocked(fetchJobLogs);

const job = {
  job_id: 'job-1',
  status: 'synthesized_publish_ready',
  created_at: '2026-06-15T12:00:00Z',
  week: '2026-W24',
  article_title: 'Test',
};

const detail = {
  job_id: 'job-1',
  status: 'synthesized_publish_ready',
  created_at: '2026-06-15T12:00:00Z',
  expires_at: null,
  week: '2026-W24',
  article_url: null,
  article_title: 'Test',
  generation: null,
  publishing: null,
  lifecycle: null,
  quality_score: 0.9,
  warnings: null,
};

function logEntry(partial: Partial<LogEntry>): LogEntry {
  return {
    timestamp: '2026-06-15T12:02:00Z',
    level: 'info',
    event: 'log',
    message: null,
    detail: null,
    task_id: null,
    stage: null,
    seq: null,
    source: 'structured',
    ...partial,
  };
}

const logs: LogEntry[] = [
  logEntry({ seq: 1, level: 'info', event: 'synthesis', message: 'recording 5 segments', stage: 'synthesis' }),
  logEntry({ seq: 2, level: 'warning', event: 'synthesis', message: 'music skipped', task_id: 'mix-1' }),
  logEntry({ seq: 3, level: 'error', event: 'synthesis', message: 'synthesis failed', stage: 'synthesis' }),
];

describe('JobMonitor log viewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchJobs.mockResolvedValue({ jobs: [job], total: 1 });
    mockFetchJobDetail.mockResolvedValue(detail);
    mockFetchJobLogs.mockResolvedValue({ job_id: 'job-1', logs, total: 3, level: null, search: null });
  });

  async function openJob() {
    const user = userEvent.setup();
    render(<JobMonitor />);
    const row = await screen.findByText('job-1');
    await user.click(row);
    await waitFor(() => expect(screen.getByText('recording 5 segments')).toBeInTheDocument());
    return user;
  }

  it('renders all structured log entries with levels', async () => {
    await openJob();
    expect(screen.getByText('music skipped')).toBeInTheDocument();
    expect(screen.getByText('synthesis failed')).toBeInTheDocument();
    // level badges present on their rows
    const warnRow = screen.getByText('music skipped').closest('tr')!;
    expect(within(warnRow).getByText('warning')).toBeInTheDocument();
    const errRow = screen.getByText('synthesis failed').closest('tr')!;
    expect(within(errRow).getByText('error')).toBeInTheDocument();
  });

  it('filters by minimum level', async () => {
    const user = await openJob();
    await user.selectOptions(screen.getByLabelText('Filter logs by minimum level'), 'warning');
    expect(screen.queryByText('recording 5 segments')).not.toBeInTheDocument();
    expect(screen.getByText('music skipped')).toBeInTheDocument();
    expect(screen.getByText('synthesis failed')).toBeInTheDocument();
  });

  it('filters by search text', async () => {
    const user = await openJob();
    await user.type(screen.getByLabelText('Search logs'), 'recording');
    expect(screen.getByText('recording 5 segments')).toBeInTheDocument();
    expect(screen.queryByText('music skipped')).not.toBeInTheDocument();
    expect(screen.queryByText('synthesis failed')).not.toBeInTheDocument();
  });

  it('applies a level-based row class for visual distinction', async () => {
    await openJob();
    const errorCell = screen.getByText('synthesis failed');
    const row = errorCell.closest('tr');
    expect(row?.className).toContain('log-row-error');
  });

  it('shows an empty message when filters exclude everything', async () => {
    const user = await openJob();
    await user.type(screen.getByLabelText('Search logs'), 'no-such-text-zzz');
    expect(screen.getByText('No log entries match the current filters.')).toBeInTheDocument();
  });

  it('resets log filters when a different job is selected', async () => {
    const job2 = { ...job, job_id: 'job-2' };
    mockFetchJobs.mockResolvedValue({ jobs: [job, job2], total: 2 });
    mockFetchJobDetail.mockImplementation(async (id: string) => ({ ...detail, job_id: id }));

    const user = await openJob();
    // Apply a restrictive filter on job-1 so only error rows remain.
    await user.selectOptions(screen.getByLabelText('Filter logs by minimum level'), 'error');
    expect(screen.queryByText('recording 5 segments')).not.toBeInTheDocument();

    // Switch to a different job: the viewer remounts and starts unfiltered.
    await user.click(screen.getByText('job-2'));
    await waitFor(() =>
      expect(
        (screen.getByLabelText('Filter logs by minimum level') as HTMLSelectElement).value,
      ).toBe(''),
    );
    expect(screen.getByText('recording 5 segments')).toBeInTheDocument();
  });
});
