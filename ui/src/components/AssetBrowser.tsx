import React, { useEffect, useState } from 'react';
import { fetchJobAssets, type JobAsset } from '../api/jobs';
import { getScopedStreamUrl, resolveStreamUrl } from '../api/episodes';

/**
 * Per-job asset browser (issue #471).
 *
 * Lists the streamable media artifacts for a job and lets users preview them
 * inline: video and per-segment audio players plus a thumbnail gallery. URLs
 * come from the authenticated streaming proxy (`/api/stream/...`) and use
 * short-lived, per-asset query tokens for browser media elements.
 */
interface AssetBrowserProps {
  jobId: string;
}

function VideoAsset({ asset }: { asset: JobAsset }) {
  const resolvedUrl = useScopedAssetUrl(asset.url);
  return (
    <figure className="asset-item asset-video">
      <video className="video-player" controls preload="metadata">
        <source
          src={resolvedUrl}
          type={asset.content_type || 'video/mp4'}
        />
        Your browser does not support the video element.
      </video>
      <figcaption className="mono-text asset-caption">{asset.name}</figcaption>
    </figure>
  );
}

function AudioAsset({ asset }: { asset: JobAsset }) {
  const resolvedUrl = useScopedAssetUrl(asset.url);
  return (
    <figure className="asset-item asset-audio">
      <figcaption className="mono-text asset-caption">{asset.name}</figcaption>
      <audio className="audio-player" controls preload="metadata">
        <source
          src={resolvedUrl}
          type={asset.content_type || 'audio/mpeg'}
        />
        Your browser does not support the audio element.
      </audio>
    </figure>
  );
}

function ImageAsset({ asset }: { asset: JobAsset }) {
  const resolvedUrl = useScopedAssetUrl(asset.url);
  return (
    <figure className="asset-item asset-image">
      <a href={resolvedUrl} target="_blank" rel="noreferrer">
        <img
          className="asset-thumb"
          src={resolvedUrl}
          alt={asset.name}
          loading="lazy"
        />
      </a>
      <figcaption className="mono-text asset-caption">{asset.name}</figcaption>
    </figure>
  );
}

function useScopedAssetUrl(streamUrl: string): string {
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

const AssetBrowser: React.FC<AssetBrowserProps> = ({ jobId }) => {
  const [assets, setAssets] = useState<JobAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchJobAssets(jobId);
        if (!cancelled) setAssets(data.assets);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load assets');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (loading) return <p>Loading assets…</p>;
  if (error) return <p className="error-text">Error: {error}</p>;
  if (assets.length === 0) return <p className="muted-text">No media assets for this job yet.</p>;

  const videos = assets.filter((a) => a.kind === 'video');
  const audios = assets.filter((a) => a.kind === 'audio');
  const images = assets.filter((a) => a.kind === 'image');

  return (
    <div className="asset-browser">
      {videos.length > 0 && (
        <section>
          <h4>Video</h4>
          <div className="asset-grid">
            {videos.map((a) => (
              <VideoAsset key={a.path} asset={a} />
            ))}
          </div>
        </section>
      )}

      {audios.length > 0 && (
        <section>
          <h4>Audio</h4>
          <div className="asset-list">
            {audios.map((a) => (
              <AudioAsset key={a.path} asset={a} />
            ))}
          </div>
        </section>
      )}

      {images.length > 0 && (
        <section>
          <h4>Thumbnails</h4>
          <div className="asset-gallery">
            {images.map((a) => (
              <ImageAsset key={a.path} asset={a} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
};

export default AssetBrowser;
