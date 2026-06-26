import React, { useEffect, useState } from 'react';
import {
  fetchJobs,
  fetchJobDetail,
  fetchJobLogs,
  fetchJobProgress,
  fetchJobProgressSummary,
  type JobSummary,
  type JobDetailResponse,
  type LogEntry,
  type ProgressEvent,
  type StageProgressSummary,
} from '../api/jobs';
import StageTimeline from './StageTimeline';
import AssetBrowser from './AssetBrowser';

function badgeClass(status: string): string {
  if (status.includes('failed') || status.includes('error')) return 'badge badge-error';
  if (status.includes('ready') || status === 'published') return 'badge badge-success';
  if (status.includes('warning') || status.includes('review')) return 'badge badge-warning';
  return 'badge badge-info';
}

function StatusBadge({ status }: { status: string }) {
  return <span className={badgeClass(status)}>{status.replace(/_/g, ' ')}</span>;
}

function QualityScore({ score }: { score: number | null }) {
  if (score === null) return <span>—</span>;
  const pct = Math.round(score * 100);
  const className = pct >= 80 ? 'success-text' : pct >= 50 ? 'warning-text' : 'error-text';
  return <span className={className}>{pct}%</span>;
}

const LOG_LEVELS = ['debug', 'info', 'warning', 'error'] as const;
const LEVEL_RANK: Record<string, number> = { debug: 10, info: 20, warning: 30, error: 40 };

function levelRank(level: string): number {
  return LEVEL_RANK[level?.toLowerCase()] ?? LEVEL_RANK.info;
}

function levelBadgeClass(level: string): string {
  const normalized = level?.toLowerCase();
  if (normalized === 'error') return 'badge badge-error';
  if (normalized === 'warning') return 'badge badge-warning';
  if (normalized === 'debug') return 'badge badge-muted';
  return 'badge badge-info';
}

function LogViewer({ logs }: { logs: LogEntry[] }) {
  const [minLevel, setMinLevel] = useState<string>('');
  const [search, setSearch] = useState<string>('');

  const filtered = logs.filter((log) => {
    if (minLevel && levelRank(log.level) < levelRank(minLevel)) return false;
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      const haystack = [log.event, log.message, log.detail, log.task_id, log.stage]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });

  return (
    <div>
      <div className="log-filter-bar">
        <label className="log-filter-field">
          <span>Min level</span>
          <select
            aria-label="Filter logs by minimum level"
            value={minLevel}
            onChange={(e) => setMinLevel(e.target.value)}
          >
            <option value="">All</option>
            {LOG_LEVELS.map((lvl) => (
              <option key={lvl} value={lvl}>
                {lvl}
              </option>
            ))}
          </select>
        </label>
        <label className="log-filter-field log-filter-search">
          <span>Search</span>
          <input
            type="search"
            aria-label="Search logs"
            placeholder="Filter by message, stage, task…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <span className="log-filter-count muted-text">
          {filtered.length} / {logs.length}
        </span>
      </div>
      {filtered.length === 0 ? (
        <p className="muted-text">No log entries match the current filters.</p>
      ) : (
        <table className="styled-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Level</th>
              <th>Stage</th>
              <th>Event</th>
              <th>Message</th>
              <th>Task</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((log, i) => (
              <tr key={log.seq ?? i} className={`log-row log-row-${(log.level || 'info').toLowerCase()}`}>
                <td className="mono-text">{log.timestamp || '—'}</td>
                <td>
                  <span className={levelBadgeClass(log.level)}>{log.level || 'info'}</span>
                </td>
                <td>{log.stage || '—'}</td>
                <td>{log.event}</td>
                <td className="muted-text">{log.message || log.detail || ''}</td>
                <td className="mono-text">{log.task_id || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const JobMonitor: React.FC = () => {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<JobDetailResponse | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [progressSummary, setProgressSummary] = useState<StageProgressSummary | null>(null);
  const [progressWarning, setProgressWarning] = useState<string | null>(null);

  useEffect(() => {
    loadJobs();
  }, []);

  async function loadJobs() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJobs(20, 0);
      setJobs(data.jobs);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load jobs');
    } finally {
      setLoading(false);
    }
  }

  async function selectJob(jobId: string) {
    setDetailError(null);
    setProgressWarning(null);
    setLogsLoading(true);
    setProgressEvents([]);
    setProgressSummary(null);
    try {
      // Progress is optional: a job may have no progress document yet. Capture
      // failures separately so an errored progress call surfaces a warning
      // instead of being indistinguishable from "no progress" (#474 review),
      // while job detail and logs still render.
      let progressFailed = false;
      const onProgressError = () => {
        progressFailed = true;
        return null;
      };
      const [detail, logsData, progress, summary] = await Promise.all([
        fetchJobDetail(jobId),
        fetchJobLogs(jobId),
        fetchJobProgress(jobId).catch(onProgressError),
        fetchJobProgressSummary(jobId).catch(onProgressError),
      ]);
      setSelectedJob(detail);
      setLogs(logsData.logs);
      setProgressEvents(progress?.events ?? []);
      setProgressSummary(summary);
      if (progressFailed) {
        setProgressWarning('Stage progress could not be loaded; the timeline may be incomplete.');
      }
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : 'Failed to load job detail');
    } finally {
      setLogsLoading(false);
    }
  }

  function handleJobRowKeyDown(event: React.KeyboardEvent<HTMLTableRowElement>, jobId: string) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      void selectJob(jobId);
    }
  }

  if (loading) return <p>Loading jobs…</p>;
  if (error) return <p className="error-text">Error: {error}</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Pipeline Jobs</h1>
          <p className="page-subtitle">{total} jobs available for inspection.</p>
        </div>
      </div>

      <table className="styled-table">
        <thead>
          <tr>
            <th>Job ID</th>
            <th>Status</th>
            <th>Week</th>
            <th>Title</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr
              key={job.job_id}
              onClick={() => selectJob(job.job_id)}
              onKeyDown={(event) => handleJobRowKeyDown(event, job.job_id)}
              role="button"
              tabIndex={0}
              className={`row-button${selectedJob?.job_id === job.job_id ? ' is-active' : ''}`}
            >
            <td className="mono-text">{job.job_id}</td>
            <td>
              <StatusBadge status={job.status} />
            </td>
            <td>{job.week || '—'}</td>
            <td>{job.article_title || '—'}</td>
            <td className="mono-text">{job.created_at || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {detailError && (
        <p className="error-text section-spacing">Error: {detailError}</p>
      )}

      {selectedJob && (
        <div className="card panel-spacing">
          <div className="page-header">
            <div>
             <h2>Job: {selectedJob.job_id}</h2>
             <p className="page-subtitle">
               Quality score <QualityScore score={selectedJob.quality_score} />
             </p>
            </div>
          </div>
          <dl className="detail-list">
            <dt>Status</dt>
            <dd><StatusBadge status={selectedJob.status} /></dd>
            <dt>Week</dt>
            <dd>{selectedJob.week || '—'}</dd>
            <dt>Article</dt>
            <dd>
              {selectedJob.article_url ? (
                <a href={selectedJob.article_url} target="_blank" rel="noreferrer">
                  {selectedJob.article_title || selectedJob.article_url}
                </a>
              ) : (
                '—'
              )}
            </dd>
            <dt>Created</dt>
            <dd>{selectedJob.created_at || '—'}</dd>
            <dt>Expires</dt>
            <dd>{selectedJob.expires_at || '—'}</dd>
            {selectedJob.warnings && selectedJob.warnings.length > 0 && (
              <>
                <dt>Warnings</dt>
                <dd>
                  <ul>
                    {selectedJob.warnings.map((w, i) => (
                      <li className="warning-text" key={i}>{w}</li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
          </dl>

          <h3>Pipeline Stages</h3>
          {progressWarning && (
            <p className="warning-text section-spacing" role="status">{progressWarning}</p>
          )}
          <StageTimeline events={progressEvents} summary={progressSummary} />

          <h3>Assets</h3>
          <AssetBrowser jobId={selectedJob.job_id} />

          <h3>Logs</h3>
          {logsLoading ? (
            <p>Loading logs…</p>
          ) : logs.length > 0 ? (
            <LogViewer key={selectedJob.job_id} logs={logs} />
          ) : (
            <p>No log entries.</p>
          )}
        </div>
      )}
    </div>
  );
};

export default JobMonitor;
