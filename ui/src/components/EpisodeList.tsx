import React, { useEffect, useState } from 'react';
import {
  fetchEpisodes,
  getScopedStreamUrl,
  resolveStreamUrl,
  type Episode,
} from '../api/episodes';

function badgeClass(status: string): string {
  if (status === 'published') return 'badge badge-success';
  if (status.includes('failed') || status.includes('error')) return 'badge badge-error';
  if (status.includes('ready') || status.includes('review')) return 'badge badge-warning';
  return 'badge badge-info';
}

function AudioPlayer({ audioUrl }: { audioUrl: string }) {
  const resolvedUrl = useScopedStreamUrl(audioUrl);

  return (
    <audio className="audio-player" controls preload="metadata">
      <source src={resolvedUrl} type="audio/mpeg" />
      Your browser does not support the audio element.
    </audio>
  );
}

function VideoPlayer({ videoUrl }: { videoUrl: string }) {
  const resolvedUrl = useScopedStreamUrl(videoUrl);

  return (
    <video
      className="video-player"
      controls
      preload="metadata"
      style={{ width: "100%", maxWidth: "640px" }}
    >
      <source src={resolvedUrl} type="video/mp4" />
      Your browser does not support the video element.
    </video>
  );
}

function useScopedStreamUrl(streamUrl: string): string {
  const [scopedUrl, setScopedUrl] = useState<{ streamUrl: string; url: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getScopedStreamUrl(streamUrl)
      .then((url) => {
        if (!cancelled) setScopedUrl({ streamUrl, url });
      })
      .catch(() => {
        if (!cancelled) setScopedUrl({ streamUrl, url: resolveStreamUrl(streamUrl) });
      });
    return () => {
      cancelled = true;
    };
  }, [streamUrl]);

  return scopedUrl?.streamUrl === streamUrl ? scopedUrl.url : resolveStreamUrl(streamUrl);
}

function ScopedDownloadLink({
  streamUrl,
  children,
}: {
  streamUrl: string;
  children: React.ReactNode;
}) {
  const resolvedUrl = useScopedStreamUrl(streamUrl);
  return (
    <a className="btn btn-secondary" href={resolvedUrl} download>
      {children}
    </a>
  );
}

const EpisodeList: React.FC = () => {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

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

  useEffect(() => {
    void Promise.resolve().then(loadEpisodes);
  }, []);

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
  if (error) return <p className="error-text">Error: {error}</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Episodes</h1>
          <p className="page-subtitle">{total} episodes available for review and playback.</p>
        </div>
      </div>

      <table className="styled-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Status</th>
            <th>Quality</th>
            <th>Publish Status</th>
            <th>Created</th>
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
                className={`row-button${expandedId === ep.job_id ? ' is-active' : ''}`}
              >
                <td>
                  <div className="episode-title">{ep.title || 'Untitled episode'}</div>
                </td>
                <td>
                  <span className={badgeClass(ep.status)}>{ep.status.replace(/_/g, ' ')}</span>
                </td>
                <td>
                  {ep.quality_score !== null
                    ? `${Math.round(ep.quality_score * 100)}%`
                    : '—'}
                </td>
                <td>{ep.publish_status || '—'}</td>
                <td className="mono-text">{ep.created_at || '—'}</td>
              </tr>
              {expandedId === ep.job_id && (
                <tr>
                  <td className="table-detail-cell" colSpan={5}>
                    <strong>Audio Preview</strong>
                    <div className="audio-preview">
                      {ep.audio_url ? (
                        <>
                          <AudioPlayer audioUrl={ep.audio_url} />
                          <ScopedDownloadLink streamUrl={ep.audio_url}>
                            Download MP3
                          </ScopedDownloadLink>
                        </>
                      ) : (
                        <span className="muted-text">No audio file available</span>
                      )}
                    </div>

                    {ep.video_url && (
                      <div className="video-preview">
                        <strong>Video Preview</strong>
                        <div className="video-preview-body">
                          <VideoPlayer videoUrl={ep.video_url} />
                          <ScopedDownloadLink streamUrl={ep.video_url}>
                            Download MP4
                          </ScopedDownloadLink>
                        </div>
                      </div>
                    )}

                    {ep.artifacts && ep.artifacts.length > 0 && (
                      <div className="artifacts-section">
                        <strong>Artifacts</strong>
                        <div className="artifacts-list">
                          {ep.artifacts.map((artifact) => (
                            <ScopedDownloadLink
                              key={artifact.path}
                              streamUrl={artifact.url}
                            >
                              Download {artifact.name}
                            </ScopedDownloadLink>
                          ))}
                        </div>
                      </div>
                    )}
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
