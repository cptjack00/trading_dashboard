import { useCallback, useEffect, useState } from 'react'
import ConfigRoots from './ConfigRoots'
import NewRun from './NewRun'
import RunComparison from './RunComparison'
import RunList, { useRuns, type Run } from './RunList'
import RunOverview from './RunOverview'
import Topstrip from './Topstrip'

function runKey(run: Run): string {
  return `${run.project}-${run.run_id}`
}

type AuthState = 'checking' | 'authenticated' | 'anonymous'

async function checkSession(): Promise<boolean> {
  const res = await fetch('/api/session')
  return res.ok
}

function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    setSubmitting(false)
    if (res.ok) {
      onLoggedIn()
    } else {
      setError('Incorrect password')
    }
  }

  return (
    <main className="login">
      <form onSubmit={handleSubmit}>
        <h1>Signal Deck</h1>
        <label htmlFor="password">Shared secret</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={submitting || !password}>
          Log in
        </button>
      </form>
    </main>
  )
}

function DashboardShell({ onLoggedOut }: { onLoggedOut: () => void }) {
  const { runs, refresh: refreshRuns } = useRuns()
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  // Total pnl for whichever run's Overview is currently open, pushed live off
  // its SSE stream - overrides that one run's poll-derived `pnl` so the rail
  // doesn't sit on a stale number for up to 60s while its own tab is live.
  const [livePnl, setLivePnl] = useState<{ key: string; pnl: number } | null>(null)
  const [compareMode, setCompareMode] = useState(false)
  const [compareSelection, setCompareSelection] = useState<Run[]>([])
  const [comparing, setComparing] = useState<Run[] | null>(null)
  const [showConfigRoots, setShowConfigRoots] = useState(false)
  const [showNewRun, setShowNewRun] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)

  const selectedRun = selectedKey ? runs.find((r) => runKey(r) === selectedKey) ?? null : null
  const onLivePnl = useCallback(
    (pnl: number | null) => setLivePnl(pnl === null || !selectedKey ? null : { key: selectedKey, pnl }),
    [selectedKey],
  )
  const runsForDisplay = livePnl ? runs.map((r) => (runKey(r) === livePnl.key ? { ...r, pnl: livePnl.pnl } : r)) : runs

  function selectRun(run: Run) {
    setComparing(null)
    setSelectedKey(runKey(run))
  }

  function toggleCompareSelection(run: Run) {
    setCompareSelection((prev) => {
      if (prev.some((r) => runKey(r) === runKey(run))) return prev.filter((r) => runKey(r) !== runKey(run))
      if (prev.length >= 4) return prev
      return [...prev, run]
    })
  }

  function exitCompareMode() {
    setCompareMode(false)
    setCompareSelection([])
  }

  async function handleLogout() {
    await fetch('/api/logout', { method: 'POST' })
    onLoggedOut()
  }

  return (
    <div className="shell">
      <Topstrip runs={runsForDisplay} />
      <div className="workspace">
        {!railCollapsed && (
          <aside className="rail">
            <button className="rail-collapse" onClick={() => setRailCollapsed(true)} title="Collapse panel">
              ⟨⟨
            </button>
            <div className="rail-head">
              <span className="eyebrow">Runs</span>
              <div className="rail-actions">
                <button className="new-run-btn" onClick={refreshRuns} title="Rescan runs now">
                  ⟳ Rescan
                </button>
                <button
                  className={`new-run-btn${compareMode ? ' on' : ''}`}
                  onClick={() => (compareMode ? exitCompareMode() : setCompareMode(true))}
                >
                  ⇄ Compare
                </button>
                <button className="new-run-btn" onClick={() => setShowNewRun(true)}>
                  + New run
                </button>
              </div>
            </div>
            <RunList
              runs={runsForDisplay}
              activeKey={selectedKey ?? undefined}
              onSelectRun={selectRun}
              compareMode={compareMode}
              selectedKeys={new Set(compareSelection.map(runKey))}
              onToggleCompare={toggleCompareSelection}
            />
            {compareMode && (
              <div className="compare-bar">
                <span>{compareSelection.length} of 2–4 runs selected</span>
                <button className="btn-mini go" disabled={compareSelection.length < 2} onClick={() => setComparing(compareSelection)}>
                  Compare
                </button>
                <button className="btn-mini" onClick={exitCompareMode}>
                  Cancel
                </button>
              </div>
            )}
            <div className="rail-foot">
              <button className="new-run-btn" onClick={() => setShowConfigRoots(true)}>
                Config roots
              </button>
              <button className="new-run-btn" onClick={handleLogout}>
                Log out
              </button>
            </div>
          </aside>
        )}
        <main className="stage">
          {railCollapsed && (
            <button className="rail-expand" onClick={() => setRailCollapsed(false)} title="Expand panel">
              ⟩⟩
            </button>
          )}
          {comparing ? (
            <RunComparison
              runs={comparing}
              onBack={() => {
                setComparing(null)
                exitCompareMode()
              }}
            />
          ) : selectedRun ? (
            <RunOverview key={runKey(selectedRun)} run={selectedRun} onLivePnl={onLivePnl} />
          ) : (
            <p className="scope-empty">Select a run from the left, or start a new one.</p>
          )}
        </main>
      </div>

      {showNewRun && <NewRun onBack={() => setShowNewRun(false)} onStarted={() => setShowNewRun(false)} />}
      {showConfigRoots && <ConfigRoots onBack={() => setShowConfigRoots(false)} />}
    </div>
  )
}

export default function App() {
  const [auth, setAuth] = useState<AuthState>('checking')

  useEffect(() => {
    checkSession().then((ok) => setAuth(ok ? 'authenticated' : 'anonymous'))
  }, [])

  if (auth === 'checking') return null
  if (auth === 'anonymous') {
    return <Login onLoggedIn={() => setAuth('authenticated')} />
  }
  return <DashboardShell onLoggedOut={() => setAuth('anonymous')} />
}
