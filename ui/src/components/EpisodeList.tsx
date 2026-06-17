import React, { useEffect, useState } from 'react';
import {
  fetchEpisodes,
  resolveAudioUrl,
  type Episode,
} from '../api/episodes';

function AudioPlayer({ audioUrl }: { audioUrl: string }) {
  const resolvedUrl = resolveAudioUrl(audioUrl);

  return (
    <audio
      controls
      preload="metadata"
      style={{ width: '100%', maxWidth: '400px' }}
    >
      <source src={resolvedUrl} type="audio/mpeg" />
      Your browser does not support the audio element.
    </audio>
  );
}

const EpisodeList: React.FC = () => {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    loadEpisodes();
  }, []);

  async function loadEpisodes() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchEpisodes(20, 0);
      setEpisodes(data.episodes);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load episodes');
    } finally {
      setLoading(false);
    }
  }

  function toggleExpand(jobId: string) {
    setExpandedId((prev) => (prev === jobId ? null : jobId));
  }

  function handleRowKeyDown(event: React.KeyboardEvent<HTMLTableRowElement>, jobId: string) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggleExpand(jobId);
    }
  }

  if (loading) return <p>Loading episodes…</p>;
  if (error) return <p style={{ color: 'red' }}>Error: {error}</p>;

  return (
    <div>
      <h2>Episodes ({total})</h2>

      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #333' }}>
            <th style={{ textAlign: 'left', padding: '8px' }}>Title</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Status</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Quality</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Publish Status</th>
            <th style={{ textAlign: 'left', padding: '8px' }}>Created</th>
          </tr>
        </thead>
        <tbody>
          {episodes.map((ep) => (
            <React.Fragment key={ep.job_id}>
              <tr
                onClick={() => toggleExpand(ep.job_id)}
                onKeyDown={(event) => handleRowKeyDown(event, ep.job_id)}
                role="button"
                tabIndex={0}
                style={{
                  cursor: 'pointer',
                  borderBottom: '1px solid #eee',
                  backgroundColor: expandedId === ep.job_id ? '#f0f7ff' : undefined,
                }}
              >
                <td style={{ padding: '8px' }}>{ep.title || '—'}</td>
                <td style={{ padding: '8px' }}>
                  <span
                    style={{
                      padding: '2px 8px',
                      borderRadius: '4px',
                      fontSize: '0.8em',
                      backgroundColor: ep.status === 'published' ? '#4CAF50' : '#FF9800',
                      color: '#fff',
                    }}
                  >
                    {ep.status.replace(/_/g, ' ')}
                  </span>
                </td>
                <td style={{ padding: '8px' }}>
                  {ep.quality_score !== null
                    ? `${Math.round(ep.quality_score * 100)}%`
                    : '—'}
                </td>
                <td style={{ padding: '8px' }}>{ep.publish_status || '—'}</td>
                <td style={{ padding: '8px', fontSize: '0.85em' }}>
                  {ep.created_at || '—'}
                </td>
              </tr>
              {expandedId === ep.job_id && (
                <tr>
                  <td colSpan={5} style={{ padding: '16px', backgroundColor: '#fafafa' }}>
                    <strong>Audio Preview</strong>
                    <div style={{ marginTop: '8px' }}>
                      {ep.audio_url ? (
                        <AudioPlayer audioUrl={ep.audio_url} />
                      ) : (
                        <span style={{ color: '#999' }}>No audio file available</span>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default EpisodeList;
