import type { ProgressEvent, StageProgressSummary } from '../api/jobs';
import { buildStageRows, PIPELINE_STAGES } from './stageTimeline';

/**
 * Pure aggregation logic for the historical run comparison dashboard (issue #475).
 *
 * Turns the per-job progress streams (#469) + stage summaries (#470) into a
 * side-by-side comparison of stage timings and key metrics across recent runs,
 * including per-stage and total deltas versus the chronologically previous run
 * so regressions/improvements over time are visible at a glance.
 */

export interface RunInput {
  jobId: string;
  title: string | null;
  createdAt: string | null;
  status: string;
  qualityScore: number | null;
  events: ProgressEvent[];
  summary: StageProgressSummary | null;
}

export interface RunComparisonRow {
  jobId: string;
  title: string | null;
  createdAt: string | null;
  status: string;
  qualityScore: number | null;
  /** Observed wall-clock duration per pipeline stage (ms), null when not run. */
  stageDurations: Record<string, number | null>;
  /** Wall-clock total from first stage start to last stage end (ms). */
  totalDurationMs: number | null;
  /** Total delta vs the chronologically previous run (ms); negative = faster. */
  totalDeltaMs: number | null;
  /** Per-stage delta vs the chronologically previous run (ms). */
  stageDeltas: Record<string, number | null>;
}

function createdMs(run: RunInput): number {
  const ms = run.createdAt ? Date.parse(run.createdAt) : NaN;
  return Number.isNaN(ms) ? 0 : ms;
}

function delta(current: number | null, previous: number | null): number | null {
  if (current === null || previous === null) return null;
  return current - previous;
}

/**
 * Build comparison rows for the supplied runs, newest first.
 *
 * `now` is forwarded to the stage-duration derivation so an in-flight run's
 * current stage gets a stable elapsed value in tests.
 */
export function buildComparison(runs: RunInput[], now?: number): RunComparisonRow[] {
  // Chronological (oldest first) so each run can diff against the previous one.
  const chronological = [...runs].sort((a, b) => createdMs(a) - createdMs(b));

  const computed = chronological.map((run) => {
    const rows = buildStageRows(run.events, run.summary, now);
    const stageDurations: Record<string, number | null> = {};
    for (const r of rows) stageDurations[r.stage] = r.durationMs;

    const starts = rows.map((r) => r.startMs).filter((v): v is number => v !== null);
    const ends = rows.map((r) => r.endMs).filter((v): v is number => v !== null);
    const totalDurationMs =
      starts.length && ends.length ? Math.max(...ends) - Math.min(...starts) : null;

    return { run, stageDurations, totalDurationMs };
  });

  const result: RunComparisonRow[] = computed.map((entry, index) => {
    const prev = index > 0 ? computed[index - 1] : null;
    const stageDeltas: Record<string, number | null> = {};
    for (const { stage } of PIPELINE_STAGES) {
      stageDeltas[stage] = prev
        ? delta(entry.stageDurations[stage], prev.stageDurations[stage])
        : null;
    }
    return {
      jobId: entry.run.jobId,
      title: entry.run.title,
      createdAt: entry.run.createdAt,
      status: entry.run.status,
      qualityScore: entry.run.qualityScore,
      stageDurations: entry.stageDurations,
      totalDurationMs: entry.totalDurationMs,
      totalDeltaMs: prev ? delta(entry.totalDurationMs, prev.totalDurationMs) : null,
      stageDeltas,
    };
  });

  // Present newest first.
  return result.reverse();
}

export function formatDelta(ms: number | null): string {
  if (ms === null || ms === 0) return '';
  const sign = ms < 0 ? '−' : '+';
  const sec = Math.round(Math.abs(ms) / 1000);
  if (sec < 60) return `${sign}${sec}s`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return rem ? `${sign}${min}m ${rem}s` : `${sign}${min}m`;
}

/** A faster run (negative delta) is an improvement; slower is a regression. */
export function deltaClass(ms: number | null): string {
  if (ms === null || ms === 0) return '';
  return ms < 0 ? 'success-text' : 'error-text';
}
