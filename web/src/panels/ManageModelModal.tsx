import { useState } from 'react'
import {
  generateSml,
  importSmlGit,
  importSmlPath,
  saveSmlToPath,
  SmlValidationFailure,
} from '../api/client'
import { useModelStore } from '../store/modelStore'

interface Props {
  onClose: () => void
}

type Tab = 'save' | 'load'

/** Saving IS generating SML and writing it to disk; loading IS importing SML
 *  from a directory or a Git repo. There is no separate proprietary state
 *  format - two parsers (a Python one here and a browser one) for the same
 *  concept would just drift out of sync with each other. */
export function ManageModelModal({ onClose }: Props) {
  const [tab, setTab] = useState<Tab>('save')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const [modelName, setModelName] = useState('my_model')
  const [savePath, setSavePath] = useState('')
  const [importPath, setImportPath] = useState('')
  const [gitRepoUrl, setGitRepoUrl] = useState('')
  const [gitBranch, setGitBranch] = useState('main')

  const sourceMeta = useModelStore((s) => s.sourceMeta)
  const nodes = useModelStore((s) => s.nodes)
  const joins = useModelStore((s) => s.joins)
  const cfg = useModelStore((s) => s.cfg)
  const calculations = useModelStore((s) => s.calculations)
  const loadModelData = useModelStore((s) => s.loadModelData)

  async function handleSave() {
    if (!sourceMeta) {
      setError('Select a data source before saving.')
      return
    }
    const schema = nodes[0]?.schema
    if (!schema) {
      setError('Add at least one table to the canvas before saving.')
      return
    }
    if (!savePath.trim()) {
      setError('Enter a directory to save the SML into.')
      return
    }
    setBusy(true)
    setError(null)
    setStatus(null)
    try {
      const { files } = await generateSml({
        modelName,
        connectionName: `con_${sourceMeta.database}_${schema}`,
        asConnection: sourceMeta.connectionId,
        database: sourceMeta.database,
        schema,
        dialect: sourceMeta.dialect,
        nodes,
        joins,
        cfg,
        calculations,
      })
      const result = await saveSmlToPath(savePath.trim(), files)
      setStatus(`Saved ${result.count} files to ${result.path}`)
    } catch (err) {
      if (err instanceof SmlValidationFailure) {
        setError(err.errors.join('\n'))
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setBusy(false)
    }
  }

  // The path field is read directly by the API server (which runs on the
  // same machine as the SML repo, unlike the browser) - no file picker needed.
  async function handleImportPath() {
    if (!importPath.trim()) {
      setError('Enter a directory to load the SML from.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await importSmlPath(importPath.trim())
      loadModelData({
        nodes: result.nodes as never[],
        joins: result.joins as never[],
        cfg: result.cfg as never,
        calculations: result.calculations as never[],
      })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleImportGit() {
    setBusy(true)
    setError(null)
    try {
      const result = await importSmlGit({ repoUrl: gitRepoUrl.trim() || undefined, branch: gitBranch.trim() })
      loadModelData({
        nodes: result.nodes as never[],
        joins: result.joins as never[],
        cfg: result.cfg as never,
        calculations: result.calculations as never[],
      })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="sml-modal" style={{ width: 560 }} onClick={(e) => e.stopPropagation()}>
        <div className="sml-modal-header">
          <div className="identity-title">Save / Load Model</div>
          <button className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div style={{ display: 'flex', borderBottom: '1px solid var(--as-hairline)' }}>
          {(['save', 'load'] as Tab[]).map((t) => (
            <div
              key={t}
              className={`sml-file-tab ${tab === t ? 'sml-file-tab-active' : ''}`}
              style={{ flex: 1, textAlign: 'center', textTransform: 'uppercase' }}
              onClick={() => setTab(t)}
            >
              {t}
            </div>
          ))}
        </div>

        <div style={{ padding: 20 }}>
          {error && (
            <div className="login-error" style={{ marginBottom: 12, whiteSpace: 'pre-wrap' }}>
              {error}
            </div>
          )}
          {status && (
            <div className="field-note" style={{ marginBottom: 12 }}>
              {status}
            </div>
          )}

          {tab === 'save' && (
            <div>
              <div className="field-note" style={{ marginBottom: 12 }}>
                Generates the SML for the current model and writes it to a directory on the machine
                running the API server.
              </div>
              <label className="field">
                Model name
                <input value={modelName} onChange={(e) => setModelName(e.target.value)} />
              </label>
              <label className="field">
                Save to directory
                <input
                  className="mono-input"
                  placeholder="/path/to/save-sml"
                  value={savePath}
                  onChange={(e) => setSavePath(e.target.value)}
                />
              </label>
              <button className="btn btn-primary" disabled={busy} onClick={handleSave}>
                {busy ? 'Saving…' : 'Generate & save'}
              </button>
            </div>
          )}

          {tab === 'load' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <div className="section-label" style={{ marginBottom: 8 }}>
                  From a local directory of SML files
                </div>
                <label className="field">
                  Path (on the machine running the API server)
                  <input
                    className="mono-input"
                    placeholder="/path/to/sml-repo"
                    value={importPath}
                    onChange={(e) => setImportPath(e.target.value)}
                  />
                </label>
                <button className="btn btn-primary" disabled={busy} onClick={handleImportPath}>
                  {busy ? 'Loading…' : 'Load from path'}
                </button>
              </div>

              <div>
                <div className="section-label" style={{ marginBottom: 8 }}>
                  From the Git repo linked in connections.yaml
                </div>
                <label className="field">
                  Repo URL (optional - defaults to connections.yaml's git.repo)
                  <input
                    className="mono-input"
                    placeholder="https://github.com/org/repo.git"
                    value={gitRepoUrl}
                    onChange={(e) => setGitRepoUrl(e.target.value)}
                  />
                </label>
                <label className="field">
                  Branch
                  <input value={gitBranch} onChange={(e) => setGitBranch(e.target.value)} />
                </label>
                <button className="btn btn-primary" disabled={busy} onClick={handleImportGit}>
                  {busy ? 'Pulling…' : 'Pull and load'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
