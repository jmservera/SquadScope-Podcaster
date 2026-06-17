import { authenticatedFetch } from './apiClient';

const API_BASE = import.meta.env.VITE_MONITORING_API_URL || '';

export type CredentialType = 'spotify' | 'youtube' | 'api_key';

export interface CredentialSummary {
  id: string;
  type: CredentialType;
  label: string;
  created_at: string;
  updated_at: string;
  /** Whether the credential has been set (value is never returned). */
  is_set: boolean;
}

export interface CredentialListResponse {
  credentials: CredentialSummary[];
}

export interface SaveCredentialPayload {
  type: CredentialType;
  label: string;
  /** Key-value pairs for this credential type (e.g. SP_DC, SP_KEY for Spotify). */
  values: Record<string, string>;
}

export async function fetchCredentials(): Promise<CredentialListResponse> {
  const resp = await authenticatedFetch(`${API_BASE}/api/credentials`);
  if (!resp.ok) throw new Error(`Failed to fetch credentials: ${resp.status}`);
  return resp.json();
}

export async function saveCredential(payload: SaveCredentialPayload): Promise<CredentialSummary> {
  const resp = await authenticatedFetch(`${API_BASE}/api/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`Failed to save credential: ${resp.status}`);
  return resp.json();
}

export async function deleteCredential(id: string): Promise<void> {
  const resp = await authenticatedFetch(
    `${API_BASE}/api/credentials/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
  if (!resp.ok) throw new Error(`Failed to delete credential: ${resp.status}`);
}
