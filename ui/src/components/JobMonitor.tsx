import React, { useEffect, useState } from 'react';
import {
  fetchJobs,
  fetchJobDetail,
  fetchJobLogs,
  type JobSummary,
  type JobDetailResponse,
  type LogEntry,
} from '../api/jobs';

const STATUS_COLORS: Record<string, string> = {
  accepted: '#2196F3',
  synthesized_publish_ready: '#4CAF50',
  synthesized_review_ready: '#FF9800',
  synthesis_failed: '#f44336',
  synthesis_skipped: '#9E9E9E',
  dry_run: '#607D8B',
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || '#757575';
  return (
    <span
      style={{
        backgroundColor: color,
        color: '#fff',
        padding: '2px 8px',
        borderRadius: '4px',
        fontSize: '0.8em',
      }}
    >
      {status.replace(/_/g, ' ')}
    </span>
  );
}

function QualityScore({ score }: { score: number | null }) {
  if (score === null) return <span>—</span>;
  const pct = Math.round(score * 100);
  const color = pct >= 80 ? '#4CAF50' : pct >= 50 ? '#FF9800' : '#f44336';
  return <span style={{ color, fontWeight: 'bold' }}>{pct}%</span>;
}

function LogViewer({ logs }: { logs: LogEntry[] }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85em' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid #ddd' }}>
          <th style={{ textAlign: 'left', padding: '4px 8px' }}>Time</th>
          <th style={{ textAlign: 'left', padding: '4px 8px' }}>Event</th>
          <th style={{ textAlign: 'left', padding: '4px 8px' }}>Detail</th>
        </tr>
      </thead>
      <tbody>
        {logs.map((log, i) => (
          <tr key={i} style={{ borderBottom: '1px solid #eee' }}>
            <td style={{ padding: '4px 8px', fontFamily: 'monospace' }}>
              {log.timestamp || '—'}
            </td>
            <td style={{ padding: '4px 8px' }}>{log.event}</td>
            <td style={{ padding: '4px 8px', color: '#666' }}>{log.detail || ''}</td>
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
    setError(null);
    setLogsLoading(true);
    try {
      const [detail, logsData] = await Promise.all([
        fetchJobDetail(jobId),
        fetchJobLogs(jobId),
      ]);
      setSelectedJob(detail);
      setLogs(logsData.logs);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load job detail');
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
  if (error) return <p style={{ color: 'red' }}>Error: {error}</p>;

  return (
    <div>
      <h2>Pipeline Jobs ({total})</h2>

      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #333' }}>
            <th style={{ textAlign: 'left', padding: '8px' }}>Job ID</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Status</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Week</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Title</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Created</th>
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
              style={{
                cursor: 'pointer',
                borderBottom: '1px solid #eee',
                backgroundColor:
                  selectedJob?.job_id === job.job_id ? '#f0f7ff' : undefined,
              }}
            >
              <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: '0.85em' }}>
                {job.job_id}
              </td>
              <td style={{ padding: '8px' }}>
                <StatusBadge status={job.status} />
              </td>
              <td style={{ padding: '8px' }}>{job.week || '—'}</td>
              <td style={{ padding: '8px' }}>{job.article_title || '—'}</td>
              <td style={{ padding: '8px', fontSize: '0.85em' }}>
                {job.created_at || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedJob && (
        <div style={{ marginTop: '24px' }}>
          <h3>
            Job: {selectedJob.job_id}{' '}
            <QualityScore score={selectedJob.quality_score} />
          </h3>
          <dl>
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
                  <ul style={{ margin: 0, paddingLeft: '16px' }}>
                    {selectedJob.warnings.map((w, i) => (
                      <li key={i} style={{ color: '#FF9800' }}>{w}</li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
          </dl>

          <h4>Logs</h4>
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
