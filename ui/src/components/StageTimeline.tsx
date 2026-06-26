import React from 'react';
import type { ProgressEvent, StageProgressSummary } from '../api/jobs';
import {
  buildStageRows,
  formatStageDuration,
  STAGE_STATUS_LABEL,
  stageStatusBadge,
} from './stageTimeline';

/**
 * Pipeline stage visualization (issue #474).
 *
 * Renders a timeline/Gantt view of the pipeline stages
 * (brief → script → record → compose → mux → publish) derived from the durable
 * progress event stream (#469) and the stage-progress summary (#470). Each stage
 * shows whether it is completed, in-progress, pending or skipped and — for
 * stages that ran — its observed duration.
 */
interface StageTimelineProps {
  events: ProgressEvent[];
  summary: StageProgressSummary | null;
  now?: number;
}

const StageTimeline: React.FC<StageTimelineProps> = ({ events, summary, now }) => {
  const rows = buildStageRows(events, summary ?? null, now);
  const hasProgress = events.length > 0 || (summary && summary.stage !== null);

  if (!hasProgress) {
    return <p className="muted-text">No stage progress recorded yet.</p>;
  }

  // Timeline window for proportional bar placement.
  const starts = rows.map((r) => r.startMs).filter((v): v is number => v !== null);
  const ends = rows.map((r) => r.endMs ?? r.startMs).filter((v): v is number => v !== null);
  const windowStart = starts.length ? Math.min(...starts) : 0;
  const windowEnd = ends.length ? Math.max(...ends) : windowStart + 1;
  const span = Math.max(1, windowEnd - windowStart);

  return (
    <div className="stage-timeline" role="list" aria-label="Pipeline stage timeline">
      {rows.map((row) => {
        const left = row.startMs !== null ? ((row.startMs - windowStart) / span) * 100 : 0;
        const rawWidth =
          row.startMs !== null && row.endMs !== null
            ? ((row.endMs - row.startMs) / span) * 100
            : 0;
        const width = row.startMs !== null ? Math.max(rawWidth, 2) : 0;
        return (
          <div
            className={`stage-row stage-${row.status}`}
            role="listitem"
            key={row.stage}
            aria-label={`${row.label}: ${STAGE_STATUS_LABEL[row.status]}`}
          >
            <div className="stage-label">{row.label}</div>
            <div className="stage-track">
              {row.startMs !== null && (
                <div
                  className={`stage-bar stage-bar-${row.status}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                />
              )}
            </div>
            <div className="stage-meta">
              <span className={`badge ${stageStatusBadge(row.status)}`}>
                {STAGE_STATUS_LABEL[row.status]}
              </span>
              <span className="mono-text stage-duration">{formatStageDuration(row.durationMs)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default StageTimeline;
