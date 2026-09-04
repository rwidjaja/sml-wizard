import { useEffect, useState } from 'react'
import { checkSession, fetchSavedConnections, login } from '../api/client'
import { useSessionStore } from '../store/sessionStore'

export function LoginScreen() {
  const [url, setUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [apiToken, setApiToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [checking, setChecking] = useState(true)
  const [savedConnections, setSavedConnections] = useState<string[]>([])
  const [selectedConnection, setSelectedConnection] = useState('')
  const [showManualForm, setShowManualForm] = useState(false)
  const { error, setError, setAuthenticated } = useSessionStore()

  // On load: if this browser already has a live server-side session (e.g. a
  // page refresh), skip straight past the login screen. Otherwise, if
  // connections.yaml has saved connections, offer (and auto-try) those
  // instead of always starting from a blank form.
  useEffect(() => {
    let cancelled = false
    async function init() {
      try {
        const { authenticated } = await checkSession()
        if (authenticated) {
          if (!cancelled) setAuthenticated(Date.now() / 1000 + 3600)
          return
        }
      } catch {
        // fall through to the saved-connections / manual flow
      }
      try {
        const { names } = await fetchSavedConnections()
        if (cancelled) return
        setSavedConnections(names)
        if (names.length > 0) {
          setSelectedConnection(names[0])
          await connectWith(names[0])
        } else {
          setShowManualForm(true)
        }
      } catch {
        if (!cancelled) setShowManualForm(true)
      } finally {
        if (!cancelled) setChecking(false)
      }
    }
    init()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function connectWith(connectionName: string) {
    setBusy(true)
    setError(null)
    try {
      const { expiresAt } = await login({ connectionName })
      setAuthenticated(expiresAt)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setShowManualForm(true)
    } finally {
      setBusy(false)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { expiresAt } = await login({
        url,
        username: username || undefined,
        password: password || undefined,
        apiToken: apiToken || undefined,
        insecure: true,
      })
      setAuthenticated(expiresAt)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (checking && !showManualForm) {
    return (
      <div className="login-shell">
        <div className="login-card" style={{ textAlign: 'center', color: 'var(--as-muted-56)' }}>
          Connecting…
        </div>
      </div>
    )
  }

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="eyebrow">ATSCALE · SML WIZARD</div>
        <h1 className="headline">
          Connect to <em>AtScale</em>
        </h1>

        {savedConnections.length > 0 && (
          <div className="saved-connections">
            <label>
              Saved connection
              <select
                className="source-select"
                value={selectedConnection}
                onChange={(e) => setSelectedConnection(e.target.value)}
              >
                {savedConnections.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => connectWith(selectedConnection)}
            >
              {busy ? 'Connecting…' : `Connect using "${selectedConnection}"`}
            </button>
            {!showManualForm && (
              <button type="button" className="btn btn-ghost" onClick={() => setShowManualForm(true)}>
                Use a different login instead
              </button>
            )}
          </div>
        )}

        {error && <div className="login-error">{error}</div>}

        {showManualForm && (
          <form onSubmit={submit} className="manual-login-form">
            <label>
              Hostname
              <input
                type="text"
                placeholder="https://your-instance.atscaledomain.com"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </label>
            <label>
              Username
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <label>
              API token (optional, preferred)
              <input type="password" value={apiToken} onChange={(e) => setApiToken(e.target.value)} />
            </label>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
