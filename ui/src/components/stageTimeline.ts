import type { ProgressEvent, StageProgressSummary } from '../api/jobs';

/**
 * Pure stage-timeline derivation logic (issue #474), kept in its own module so
 * the component file only exports a component (React Fast Refresh friendly).
 *
 * Derives a timeline/Gantt view of the pipeline stages
 * (brief → script → record → compose → mux → publish) from the durable progress
 * event stream (#469) and the stage-progress summary (#470).
 */

export type StageStatus = 'completed' | 'in_progress' | 'pending' | 'skipped' | 'failed';

export interface StageRow {
  stage: string;
  label: string;
  status: StageStatus;
  startMs: number | null;
  endMs: number | null;
  durationMs: number | null;
}

/** Canonical working stages shown on the timeline, in pipeline order. */
export const PIPELINE_STAGES: { stage: string; label: string }[] = [
  { stage: 'brief', label: 'Brief' },
  { stage: 'script', label: 'Script' },
  { stage: 'synthesis', label: 'Record' },
  { stage: 'compose', label: 'Compose' },
  { stage: 'mux', label: 'Mux' },
  { stage: 'publish', label: 'Publish' },
];

const WORKING_ORDER = new Map(PIPELINE_STAGES.map((s, i) => [s.stage, i]));
const TERMINAL_STAGES = new Set(['completed', 'failed']);

interface Interval {
  stage: string;
  startMs: number;
  endMs: number | null;
}

function parseMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/** Collapse the ordered event stream into one interval per contiguous stage run. */
function buildIntervals(events: ProgressEvent[]): Interval[] {
  const parsed = events
    .map((e) => ({ stage: e.stage, seq: e.seq, ms: parseMs(e.at) }))
    .filter((e): e is { stage: string; seq: number; ms: number } => e.ms !== null && !!e.stage)
    .sort((a, b) => a.ms - b.ms || a.seq - b.seq);

  const intervals: Interval[] = [];
  for (const e of parsed) {
    const last = intervals[intervals.length - 1];
    if (last && last.stage === e.stage) continue;
    if (last && last.endMs === null) last.endMs = e.ms;
    intervals.push({ stage: e.stage, startMs: e.ms, endMs: null });
  }
  return intervals;
}

/**
 * Derive per-stage timeline rows from the progress events and summary.
 *
 * Pure and deterministic: pass `now` for stable rendering/tests. Returns one row
 * per canonical pipeline stage so the UI can always show the full pipeline shape.
 */
export function buildStageRows(
  events: ProgressEvent[],
  summary: StageProgressSummary | null,
  now: number = Date.now()
): StageRow[] {
  const intervals = buildIntervals(events);

  // First interval per working stage (stages should not normally repeat).
  const firstByStage = new Map<string, Interval>();
  for (const iv of intervals) {
    if (WORKING_ORDER.has(iv.stage) && !firstByStage.has(iv.stage)) {
      firstByStage.set(iv.stage, iv);
    }
  }

  const terminal = summary?.terminal ?? intervals.some((iv) => TERMINAL_STAGES.has(iv.stage));
  const failed = summary?.stage === 'failed' || intervals.some((iv) => iv.stage === 'failed');
  const currentStage =
    summary?.stage ?? (intervals.length ? intervals[intervals.length - 1].stage : null);

  // Close time for an open (in-progress) working stage.
  const closeMs = parseMs(summary?.updated_at) ?? now;

  // Furthest working stage index that actually started.
  let maxReached = -1;
  for (const stage of firstByStage.keys()) {
    maxReached = Math.max(maxReached, WORKING_ORDER.get(stage) ?? -1);
  }

  // The last working stage that ran is where a failure (if any) is attributed.
  const lastWorkingStage = [...firstByStage.keys()].reduce<string | null>((acc, stage) => {
    const idx = WORKING_ORDER.get(stage) ?? -1;
    const accIdx = acc === null ? -1 : WORKING_ORDER.get(acc) ?? -1;
    return idx > accIdx ? stage : acc;
  }, null);

  return PIPELINE_STAGES.map(({ stage, label }, index) => {
    const iv = firstByStage.get(stage);
    if (iv) {
      const isCurrentOpen = !terminal && stage === currentStage;
      const endMs = iv.endMs ?? (isCurrentOpen ? closeMs : null);
      let status: StageStatus = 'completed';
      if (failed && stage === lastWorkingStage) {
        status = 'failed';
      } else if (isCurrentOpen) {
        status = 'in_progress';
      }
      const durationMs = endMs !== null ? Math.max(0, endMs - iv.startMs) : null;
      return { stage, label, status, startMs: iv.startMs, endMs, durationMs };
    }

    // Stage never produced an event. Only infer "skipped" when a *later* stage
    // produced an event — that proves this earlier stage was passed. Stages at
    // or beyond the furthest reached stage with no events (e.g. on a terminal
    // job whose progress events aged out of the retained window) are left
    // neutral/"pending" rather than mislabelled "skipped" (#474 review).
    const skipped = index < maxReached;
    return {
      stage,
      label,
      status: skipped ? 'skipped' : 'pending',
      startMs: null,
      endMs: null,
      durationMs: null,
    };
  });
}

export function formatStageDuration(ms: number | null): string {
  if (ms === null) return '—';
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return sec ? `${min}m ${sec}s` : `${min}m`;
}

export const STAGE_STATUS_LABEL: Record<StageStatus, string> = {
  completed: 'Completed',
  in_progress: 'In progress',
  pending: 'Pending',
  skipped: 'Skipped',
  failed: 'Failed',
};

export function stageStatusBadge(status: StageStatus): string {
  switch (status) {
    case 'completed':
      return 'badge-success';
    case 'in_progress':
      return 'badge-info';
    case 'failed':
      return 'badge-error';
    case 'skipped':
      // Neutral grey to match the grey `.stage-bar-skipped` bar — a skipped
      // stage was not run, not an alert/warning state (#474 review).
      return 'badge-muted';
    default:
      return 'badge-muted';
  }
}
