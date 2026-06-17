import React, { useEffect, useState } from 'react';
import type { PodcastConfigData, PublishTarget } from '../api/podcastConfig';
import { fetchPodcastConfig, savePodcastConfig, uploadMusic } from '../api/podcastConfig';

interface PublishTargetWithId extends PublishTarget {
  _key: string;
}

let nextTargetKey = 0;
const genKey = () => `target-${++nextTargetKey}-${Date.now()}`;

const EMPTY_TARGET: PublishTarget = { platform: 'spotify', enabled: true, target_id: '' };

const PLATFORM_LABELS: Record<PublishTarget['platform'], string> = {
  spotify: 'Spotify Show ID',
  youtube: 'YouTube Channel ID',
  rss: 'RSS Feed URL',
};

const PodcastConfigEditor: React.FC = () => {
  const [config, setConfig] = useState<PodcastConfigData>({
    name: '',
    intro_music_url: '',
    outro_music_url: '',
    publish_targets: [],
  });
  const [targetKeys, setTargetKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [uploading, setUploading] = useState<'intro' | 'outro' | null>(null);

  useEffect(() => {
    const loadConfig = async () => {
      try {
        setLoading(true);
        const data = await fetchPodcastConfig();
        setConfig(data);
        setTargetKeys(data.publish_targets.map(() => genKey()));
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load config');
      } finally {
        setLoading(false);
      }
    };
    loadConfig();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!config.name.trim()) {
      setError('Podcast name is required');
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      const saved = await savePodcastConfig(config);
      setConfig(saved);
      setSuccess('Configuration saved successfully');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const handleMusicUpload = async (slot: 'intro' | 'outro', file: File) => {
    try {
      setUploading(slot);
      setError(null);
      const result = await uploadMusic(file, slot);
      setConfig((prev) => ({
        ...prev,
        [`${slot}_music_url`]: result.url,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload music');
    } finally {
      setUploading(null);
    }
  };

  const addTarget = () => {
    setConfig((prev) => ({
      ...prev,
      publish_targets: [...prev.publish_targets, { ...EMPTY_TARGET }],
    }));
    setTargetKeys((prev) => [...prev, genKey()]);
  };

  const removeTarget = (index: number) => {
    setConfig((prev) => ({
      ...prev,
      publish_targets: prev.publish_targets.filter((_, i) => i !== index),
    }));
    setTargetKeys((prev) => prev.filter((_, i) => i !== index));
  };

  const updateTarget = (index: number, updates: Partial<PublishTarget>) => {
    setConfig((prev) => ({
      ...prev,
      publish_targets: prev.publish_targets.map((t, i) =>
        i === index ? { ...t, ...updates } : t,
      ),
    }));
  };

  if (loading) return <p>Loading configuration…</p>;

  return (
    <div>
      <h1>Podcast Configuration</h1>

      {error && <p role="alert" style={{ color: 'red' }}>{error}</p>}
      {success && <p role="status" style={{ color: 'green' }}>{success}</p>}

      <form onSubmit={handleSave}>
        <section>
          <h2>General</h2>
          <div>
            <label htmlFor="podcast-name">Podcast Name</label>
            <input
              id="podcast-name"
              type="text"
              value={config.name}
              onChange={(e) => setConfig((prev) => ({ ...prev, name: e.target.value }))}
            />
          </div>
        </section>

        <section>
          <h2>Music</h2>

          <div>
            <label htmlFor="intro-music-url">Intro Music URL</label>
            <input
              id="intro-music-url"
              type="text"
              value={config.intro_music_url}
              onChange={(e) => setConfig((prev) => ({ ...prev, intro_music_url: e.target.value }))}
              placeholder="https://storage.example.com/intro.mp3"
            />
            <label htmlFor="intro-music-upload">
              {uploading === 'intro' ? 'Uploading…' : 'Upload intro music'}
            </label>
            <input
              id="intro-music-upload"
              type="file"
              accept="audio/*"
              disabled={uploading !== null}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleMusicUpload('intro', file);
              }}
            />
          </div>

          <div>
            <label htmlFor="outro-music-url">Outro Music URL</label>
            <input
              id="outro-music-url"
              type="text"
              value={config.outro_music_url}
              onChange={(e) => setConfig((prev) => ({ ...prev, outro_music_url: e.target.value }))}
              placeholder="https://storage.example.com/outro.mp3"
            />
            <label htmlFor="outro-music-upload">
              {uploading === 'outro' ? 'Uploading…' : 'Upload outro music'}
            </label>
            <input
              id="outro-music-upload"
              type="file"
              accept="audio/*"
              disabled={uploading !== null}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleMusicUpload('outro', file);
              }}
            />
          </div>
        </section>

        <section>
          <h2>Publish Targets</h2>
          {config.publish_targets.length === 0 && (
            <p>No publish targets configured.</p>
          )}
          {config.publish_targets.map((target, index) => (
            <fieldset key={targetKeys[index] ?? index}>
              <legend>Target {index + 1}</legend>
              <div>
                <label htmlFor={`target-platform-${index}`}>Platform</label>
                <select
                  id={`target-platform-${index}`}
                  value={target.platform}
                  onChange={(e) =>
                    updateTarget(index, { platform: e.target.value as PublishTarget['platform'] })
                  }
                >
                  <option value="spotify">Spotify</option>
                  <option value="youtube">YouTube</option>
                  <option value="rss">RSS</option>
                </select>
              </div>
              <div>
                <label htmlFor={`target-id-${index}`}>
                  {PLATFORM_LABELS[target.platform]}
                </label>
                <input
                  id={`target-id-${index}`}
                  type="text"
                  value={target.target_id}
                  onChange={(e) => updateTarget(index, { target_id: e.target.value })}
                />
              </div>
              <div>
                <label>
                  <input
                    type="checkbox"
                    checked={target.enabled}
                    onChange={(e) => updateTarget(index, { enabled: e.target.checked })}
                  />
                  Enabled
                </label>
              </div>
              <button type="button" onClick={() => removeTarget(index)}>
                Remove Target
              </button>
            </fieldset>
          ))}
          <button type="button" onClick={addTarget}>Add Publish Target</button>
        </section>

        <div style={{ marginTop: '1rem' }}>
          <button type="submit" disabled={saving}>
            {saving ? 'Saving…' : 'Save Configuration'}
          </button>
        </div>
      </form>
    </div>
  );
};

export default PodcastConfigEditor;
