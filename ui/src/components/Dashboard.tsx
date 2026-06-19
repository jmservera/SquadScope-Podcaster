import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
// MSAL import kept as dead code for future optional use (#273)
// import { useMsal } from '@azure/msal-react';
import { useAuth } from './AuthProvider';
import { fetchJobs, type JobSummary } from '../api/jobs';
import { fetchEpisodes, type Episode } from '../api/episodes';

interface DashboardState {
  totalJobs: number;
  totalEpisodes: number;
  latestJob: JobSummary | null;
  latestEpisode: Episode | null;
}

const Dashboard: React.FC = () => {
  const { username } = useAuth();
  const [state, setState] = useState<DashboardState>({
    totalJobs: 0,
    totalEpisodes: 0,
    latestJob: null,
    latestEpisode: null,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const [jobsData, episodesData] = await Promise.all([
          fetchJobs(1, 0),
          fetchEpisodes(1, 0),
        ]);
        setState({
          totalJobs: jobsData.total,
          totalEpisodes: episodesData.total,
          latestJob: jobsData.jobs[0] ?? null,
          latestEpisode: episodesData.episodes[0] ?? null,
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load dashboard');
      } finally {
        setLoading(false);
      }
    };

    void load();
  }, []);

  const recentActivity = state.latestJob?.status
    ? state.latestJob.status.replace(/_/g, ' ')
    : 'No recent jobs';

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Welcome, {username}</h1>
          <p className="page-subtitle">Monitor jobs, review episodes, and manage credentials.</p>
        </div>
      </div>

      {error && <p className="error-text">{error}</p>}

      <section className="card-grid">
        <article className="card">
          <p className="card-label">Total Jobs</p>
          <p className="card-value">{loading ? '—' : state.totalJobs}</p>
          <p className="card-meta">All pipeline runs currently visible in the dashboard.</p>
        </article>

        <article className="card">
          <p className="card-label">Total Episodes</p>
          <p className="card-value">{loading ? '—' : state.totalEpisodes}</p>
          <p className="card-meta">Published or in-progress episodes available to review.</p>
        </article>

        <article className="card">
          <p className="card-label">Recent Activity</p>
          <p className="card-value card-value-compact">
            {loading ? 'Loading…' : recentActivity}
          </p>
          <p className="card-meta">
            {state.latestJob?.job_id ? `Latest job: ${state.latestJob.job_id}` : 'No jobs yet.'}
          </p>
        </article>
      </section>

      <section className="card-grid">
        <article className="card">
          <h2 className="section-title">Latest Job</h2>
          <p className="card-meta">
            {state.latestJob?.article_title || state.latestJob?.job_id || 'No job data available.'}
          </p>
          <p className="card-meta">
            {state.latestJob?.created_at ? `Created ${state.latestJob.created_at}` : ''}
          </p>
          <p className="card-meta">
            <Link className="card-link" to="/jobs">
              View Latest Job →
            </Link>
          </p>
        </article>

        <article className="card">
          <h2 className="section-title">Latest Episode</h2>
          <p className="card-meta">
            {state.latestEpisode?.title || state.latestEpisode?.job_id || 'No episode data available.'}
          </p>
          <p className="card-meta">
            {state.latestEpisode?.created_at ? `Created ${state.latestEpisode.created_at}` : ''}
          </p>
          <p className="card-meta">
            <Link className="card-link" to="/episodes">
              View Latest Episode →
            </Link>
          </p>
        </article>

        <article className="card">
          <h2 className="section-title">Account</h2>
          <p className="card-meta">Signed in as {username}</p>
          <p className="card-meta">Use the sidebar to move between monitoring tools.</p>
        </article>
      </section>
    </div>
  );
};

export default Dashboard;
