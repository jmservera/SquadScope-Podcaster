import { authenticatedFetch } from './apiClient';
import { env } from '../env';

const API_BASE = env.VITE_MONITORING_API_URL || env.VITE_API_BASE_URL || '';

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
  level: string;
  event: string;
  message: string | null;
  detail: string | null;
  task_id: string | null;
  stage: string | null;
  seq: number | null;
  source: string;
}

export interface JobLogsResponse {
  job_id: string;
  logs: LogEntry[];
  total: number;
  level: string | null;
  search: string | null;
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

export async function fetchJobLogs(
  jobId: string,
  options: { level?: string; search?: string } = {}
): Promise<JobLogsResponse> {
  const params = new URLSearchParams();
  if (options.level) params.set('level', options.level);
  if (options.search) params.set('search', options.search);
  const query = params.toString();
  const url = `${API_BASE}/api/jobs/${encodeURIComponent(jobId)}/logs${query ? `?${query}` : ''}`;
  const resp = await authenticatedFetch(url);
  if (!resp.ok) throw new Error(`Failed to fetch logs for ${jobId}: ${resp.status}`);
  return resp.json();
}
