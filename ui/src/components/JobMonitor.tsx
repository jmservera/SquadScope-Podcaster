import React, { useEffect, useState } from 'react';
import {
  fetchJobs,
  fetchJobDetail,
  fetchJobLogs,
  type JobSummary,
  type JobDetailResponse,
  type LogEntry,
} from '../api/jobs';

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

function LogViewer({ logs }: { logs: LogEntry[] }) {
  return (
    <table className="styled-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Event</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {logs.map((log, i) => (
          <tr key={i}>
            <td className="mono-text">{log.timestamp || '—'}</td>
            <td>{log.event}</td>
            <td className="muted-text">{log.detail || ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
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
    setLogsLoading(true);
    try {
      const [detail, logsData] = await Promise.all([
        fetchJobDetail(jobId),
        fetchJobLogs(jobId),
      ]);
      setSelectedJob(detail);
      setLogs(logsData.logs);
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

          <h3>Logs</h3>
          {logsLoading ? (
            <p>Loading logs…</p>
          ) : logs.length > 0 ? (
            <LogViewer logs={logs} />
          ) : (
            <p>No log entries.</p>
          )}
        </div>
      )}
    </div>
  );
};

export default JobMonitor;
