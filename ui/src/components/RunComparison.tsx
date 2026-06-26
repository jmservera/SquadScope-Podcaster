import React, { useEffect, useState } from 'react';
import {
  fetchJobs,
  fetchJobDetail,
  fetchJobProgress,
  fetchJobProgressSummary,
} from '../api/jobs';
import { PIPELINE_STAGES, formatStageDuration } from './stageTimeline';
import {
  buildComparison,
  deltaClass,
  formatDelta,
  type RunComparisonRow,
  type RunInput,
} from './runComparison';

/**
 * Historical run comparison dashboard (issue #475).
 *
 * Aggregates per-stage timings and key metrics across the most recent runs and
 * presents them side by side so users can spot regressions/improvements over
 * time. Reuses the stage-duration derivation behind the timeline view (#474).
 */

const DEFAULT_RUN_COUNT = 5;
const MAX_RUN_COUNT = 10;

async function loadRun(jobId: string): Promise<RunInput> {
  const [detail, progress, summary] = await Promise.all([
    fetchJobDetail(jobId).catch(() => null),
    fetchJobProgress(jobId).catch(() => null),
    fetchJobProgressSummary(jobId).catch(() => null),
  ]);
  return {
    jobId,
    title: detail?.article_title ?? null,
    createdAt: detail?.created_at ?? null,
    status: detail?.status ?? summary?.stage ?? 'unknown',
    qualityScore: detail?.quality_score ?? null,
    events: progress?.events ?? [],
    summary,
  };
}

function QualityCell({ score }: { score: number | null }) {
  if (score === null) return <span className="muted-text">—</span>;
  const pct = Math.round(score * 100);
  const className = pct >= 80 ? 'success-text' : pct >= 50 ? 'warning-text' : 'error-text';
  return <span className={className}>{pct}%</span>;
}

const RunComparison: React.FC = () => {
  const [runCount, setRunCount] = useState(DEFAULT_RUN_COUNT);
  const [rows, setRows] = useState<RunComparisonRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const list = await fetchJobs(runCount, 0);
        const runs = await Promise.all(list.jobs.map((j) => loadRun(j.job_id)));
        if (!cancelled) setRows(buildComparison(runs));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load comparison');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runCount]);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Run Comparison</h1>
          <p className="page-subtitle">
            Compare per-stage timings and quality across recent runs.
          </p>
        </div>
        <label className="run-count-select">
          Runs&nbsp;
          <select
            value={runCount}
            onChange={(e) => setRunCount(Number(e.target.value))}
            aria-label="Number of runs to compare"
          >
            {[3, 5, 8, MAX_RUN_COUNT].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && <p>Loading comparison…</p>}
      {error && <p className="error-text">Error: {error}</p>}

      {!loading && !error && rows.length === 0 && <p>No runs to compare yet.</p>}

      {!loading && !error && rows.length > 0 && (
        <table className="styled-table comparison-table">
          <thead>
            <tr>
              <th scope="col">Stage</th>
              {rows.map((run) => (
                <th scope="col" key={run.jobId}>
                  <div className="comparison-run-head">
                    <span className="mono-text">{run.title || run.jobId}</span>
                    <span className="muted-text comparison-run-date">{run.createdAt || '—'}</span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PIPELINE_STAGES.map(({ stage, label }) => (
              <tr key={stage}>
                <th scope="row">{label}</th>
                {rows.map((run) => (
                  <td key={run.jobId}>
                    <span>{formatStageDuration(run.stageDurations[stage] ?? null)}</span>{' '}
                    {run.stageDeltas[stage] != null && run.stageDeltas[stage] !== 0 && (
                      <span className={`comparison-delta ${deltaClass(run.stageDeltas[stage])}`}>
                        {formatDelta(run.stageDeltas[stage])}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="comparison-total-row">
              <th scope="row">Total</th>
              {rows.map((run) => (
                <td key={run.jobId}>
                  <strong>{formatStageDuration(run.totalDurationMs)}</strong>{' '}
                  {run.totalDeltaMs != null && run.totalDeltaMs !== 0 && (
                    <span className={`comparison-delta ${deltaClass(run.totalDeltaMs)}`}>
                      {formatDelta(run.totalDeltaMs)}
                    </span>
                  )}
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Quality</th>
              {rows.map((run) => (
                <td key={run.jobId}>
                  <QualityCell score={run.qualityScore} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Status</th>
              {rows.map((run) => (
                <td key={run.jobId} className="muted-text">
                  {run.status.replace(/_/g, ' ')}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
};

export default RunComparison;
