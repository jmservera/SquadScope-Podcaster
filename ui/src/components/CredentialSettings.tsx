import React, { useCallback, useEffect, useState } from 'react';
import type {
  CredentialSummary,
  CredentialType,
  SaveCredentialPayload,
} from '../api/credentials';
import {
  fetchCredentials,
  saveCredential,
  deleteCredential,
  updateCredential,
} from '../api/credentials';

const CREDENTIAL_FIELDS: Record<CredentialType, { label: string; fields: string[] }> = {
  spotify: { label: 'Spotify', fields: ['SP_DC', 'SP_KEY'] },
  youtube: { label: 'YouTube', fields: ['client_id', 'client_secret', 'refresh_token'] },
  api_key: { label: 'API Key', fields: ['key'] },
};

const CredentialSettings: React.FC = () => {
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Form state
  const [selectedType, setSelectedType] = useState<CredentialType>('spotify');
  const [formLabel, setFormLabel] = useState('');
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const loadCredentials = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchCredentials();
      setCredentials(data.credentials);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load credentials');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCredentials();
  }, [loadCredentials]);

  const resetForm = () => {
    setFormLabel('');
    setFormValues({});
    setShowForm(false);
    setEditingId(null);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    const fields = CREDENTIAL_FIELDS[selectedType].fields;
    const hasEmpty = fields.some((f) => !formValues[f]?.trim());
    if (!formLabel.trim() || hasEmpty) {
      setError('All fields are required');
      return;
    }

    const payload: SaveCredentialPayload = {
      type: selectedType,
      label: formLabel.trim(),
      values: { ...formValues },
    };

    try {
      setSaving(true);
      setError(null);
      if (editingId) {
        await updateCredential(editingId, payload);
      } else {
        await saveCredential(payload);
      }
      resetForm();
      await loadCredentials();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save credential');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      setError(null);
      await deleteCredential(id);
      await loadCredentials();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete credential');
    }
  };

  const handleEdit = (cred: CredentialSummary) => {
    setEditingId(cred.id);
    setSelectedType(cred.type);
    setFormLabel(cred.label);
    setFormValues({});
    setShowForm(true);
  };

  const handleFieldChange = (field: string, value: string) => {
    setFormValues((prev) => ({ ...prev, [field]: value }));
  };

  if (loading) return <p>Loading credentials…</p>;

  return (
    <div>
      <h1>Credential Settings</h1>
      <p>
        Credentials are encrypted at rest and never displayed after saving.
      </p>

      {error && <p role="alert" style={{ color: 'red' }}>{error}</p>}

      <section>
        <h2>Saved Credentials</h2>
        {credentials.length === 0 ? (
          <p>No credentials configured yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Label</th>
                <th>Status</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {credentials.map((cred) => (
                <tr key={cred.id}>
                  <td>{CREDENTIAL_FIELDS[cred.type]?.label ?? cred.type}</td>
                  <td>{cred.label}</td>
                  <td>{cred.is_set ? '✓ Set' : '✗ Not set'}</td>
                  <td>{cred.updated_at}</td>
                  <td>
                    <button onClick={() => handleEdit(cred)}>Edit</button>
                    <button onClick={() => handleDelete(cred.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        {!showForm ? (
          <button onClick={() => setShowForm(true)}>Add Credential</button>
        ) : (
          <form onSubmit={handleSave}>
            <h2>{editingId ? 'Edit Credential' : 'Add Credential'}</h2>

            <div>
              <label htmlFor="cred-type">Type</label>
              <select
                id="cred-type"
                value={selectedType}
                onChange={(e) => {
                  setSelectedType(e.target.value as CredentialType);
                  setFormValues({});
                }}
              >
                {Object.entries(CREDENTIAL_FIELDS).map(([key, { label }]) => (
                  <option key={key} value={key}>{label}</option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="cred-label">Label</label>
              <input
                id="cred-label"
                type="text"
                value={formLabel}
                onChange={(e) => setFormLabel(e.target.value)}
                placeholder="e.g. Production Spotify"
              />
            </div>

            {CREDENTIAL_FIELDS[selectedType].fields.map((field) => (
              <div key={field}>
                <label htmlFor={`cred-${field}`}>{field}</label>
                <input
                  id={`cred-${field}`}
                  type="password"
                  value={formValues[field] ?? ''}
                  onChange={(e) => handleFieldChange(field, e.target.value)}
                  autoComplete="off"
                />
              </div>
            ))}

            <div>
              <button type="submit" disabled={saving}>
                {saving ? 'Saving…' : 'Save Credential'}
              </button>
              <button type="button" onClick={resetForm}>Cancel</button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
};

export default CredentialSettings;
