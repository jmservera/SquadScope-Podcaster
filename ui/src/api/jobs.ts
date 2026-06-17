import { authenticatedFetch } from './apiClient';

const API_BASE = import.meta.env.VITE_MONITORING_API_URL || '';

export interface JobSummary {
  job_id: string;
  status: string;
  created_at: string | null;
  week: string | null;
  article_title: string | null;
}

export interface JobListResponse {
  jobs: JobSummary[];
  total: number;
}

export interface JobDetailResponse {
  job_id: string;
  status: string;
  created_at: string | null;
  expires_at: string | null;
  week: string | null;
  article_url: string | null;
  article_title: string | null;
  generation: Record<string, unknown> | null;
  publishing: Record<string, unknown> | null;
  lifecycle: Record<string, unknown> | null;
  quality_score: number | null;
  warnings: string[] | null;
}

export interface LogEntry {
  timestamp: string | null;
  event: string;
  detail: string | null;
}

export interface JobLogsResponse {
  job_id: string;
  logs: LogEntry[];
}

export async function fetchJobs(limit = 20, offset = 0): Promise<JobListResponse> {
  const resp = await authenticatedFetch(
    `${API_BASE}/api/jobs?limit=${limit}&offset=${offset}`
  );
  if (!resp.ok) throw new Error(`Failed to fetch jobs: ${resp.status}`);
  return resp.json();
}

export async function fetchJobDetail(jobId: string): Promise<JobDetailResponse> {
  const resp = await authenticatedFetch(`${API_BASE}/api/jobs/${encodeURIComponent(jobId)}`);
  if (!resp.ok) throw new Error(`Failed to fetch job ${jobId}: ${resp.status}`);
  return resp.json();
}

export async function fetchJobLogs(jobId: string): Promise<JobLogsResponse> {
  const resp = await authenticatedFetch(`${API_BASE}/api/jobs/${encodeURIComponent(jobId)}/logs`);
  if (!resp.ok) throw new Error(`Failed to fetch logs for ${jobId}: ${resp.status}`);
  return resp.json();
}
