import { useRef, useState } from 'react'
import {
  generateSml,
  importSmlFiles,
  importSmlGit,
  importSmlPath,
  saveSmlToPath,
  SmlValidationFailure,
  type SmlFile,
} from '../api/client'
import { useModelStore } from '../store/modelStore'

const YAML_SUFFIXES = /\.ya?ml$/i

/** Reads every .yml/.yaml file out of a browser folder-picker's FileList -
 *  used when the path field is left blank, since a file picker never
 *  exposes an absolute filesystem path the API server could read itself. */
async function readYamlFilesFromFileList(fileList: FileList): Promise<SmlFile[]> {
  const files = Array.from(fileList).filter((f) => YAML_SUFFIXES.test(f.name))
  return Promise.all(
    files.map(async (f) => ({ name: f.webkitRelativePath || f.name, body: await f.text() })),
  )
}

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

  const folderInputRef = useRef<HTMLInputElement>(null)

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

  // A path in the field is read directly by the API server. A blank field
  // instead opens the browser's folder picker - its files' *content* is sent
  // to the API (it never hands over an absolute filesystem path).
  async function handleImportPath() {
    if (!importPath.trim()) {
      folderInputRef.current?.click()
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

  async function handleFolderChosen(e: React.ChangeEvent<HTMLInputElement>) {
    const fileList = e.target.files
    e.target.value = '' // allow re-picking the same folder later
    if (!fileList || fileList.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const files = await readYamlFilesFromFileList(fileList)
      if (files.length === 0) {
        setError('No .yml/.yaml files found in that folder.')
        return
      }
      const result = await importSmlFiles(files)
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
                  Path (on the machine running the API server) - leave blank to browse instead
                  <input
                    className="mono-input"
                    placeholder="/path/to/sml-repo"
                    value={importPath}
                    onChange={(e) => setImportPath(e.target.value)}
                  />
                </label>
                {/* @ts-expect-error webkitdirectory isn't in React's DOM typings but every
                    Chromium/WebKit/Firefox browser supports it for folder picking. */}
                <input
                  ref={folderInputRef}
                  type="file"
                  webkitdirectory=""
                  directory=""
                  multiple
                  hidden
                  onChange={handleFolderChosen}
                />
                <button className="btn btn-primary" disabled={busy} onClick={handleImportPath}>
                  {busy ? 'Loading…' : importPath.trim() ? 'Load from path' : 'Browse for a folder…'}
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
