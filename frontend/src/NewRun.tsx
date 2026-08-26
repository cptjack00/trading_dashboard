import { useEffect, useState } from 'react'

const PROJECTS = ['rustle', 'ticktrader'] as const
type Project = (typeof PROJECTS)[number]
type RunType = 'live' | 'backtest'

export default function NewRun({ onBack, onStarted }: { onBack: () => void; onStarted: () => void }) {
  const [project, setProject] = useState<Project>('rustle')
  const [runType, setRunType] = useState<RunType>('backtest')
  const [configs, setConfigs] = useState<string[]>([])
  const [config, setConfig] = useState('')
  const [armed, setArmed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch(`/api/config-scan/${project}`)
      .then((res) => (res.ok ? res.json() : { configs: [] }))
      .then((data) => {
        if (!cancelled) setConfigs(data.configs)
      })
    return () => {
      cancelled = true
    }
  }, [project])

  function selectProject(p: Project) {
    setProject(p)
    setConfig('')
    setArmed(false)
  }

  function selectRunType(t: RunType) {
    setRunType(t)
    setArmed(false)
  }

  function selectConfig(c: string) {
    setConfig(c)
    setArmed(false)
  }

  async function handleStart() {
    setStarting(true)
    setError(null)
    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project, run_type: runType, config }),
    })
    setStarting(false)
    if (res.ok) {
      onStarted()
    } else {
      const body = await res.json().catch(() => null)
      setError(body?.detail ?? 'Could not start run')
    }
  }

  const canStart = config !== '' && (runType === 'backtest' || armed)

  return (
    <div className="new-run">
      <button className="back-button" onClick={onBack}>
        ← Back
      </button>
      <h2>New run</h2>

      <section className="new-run-stage">
        <p className="new-run-label">1. Project</p>
        <div className="run-tabs">
          {PROJECTS.map((p) => (
            <button
              key={p}
              className={`run-tab${project === p ? ' run-tab--active' : ''}`}
              onClick={() => selectProject(p)}
            >
              {p}
            </button>
          ))}
        </div>

        <p className="new-run-label">2. Run type</p>
        <div className="run-tabs">
          {(['live', 'backtest'] as RunType[]).map((t) => (
            <button
              key={t}
              className={`run-tab${runType === t ? ' run-tab--active' : ''}`}
              onClick={() => selectRunType(t)}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        <p className="new-run-label">
          <label htmlFor="new-run-config">3. Config</label>
        </p>
        <select id="new-run-config" value={config} onChange={(e) => selectConfig(e.target.value)}>
          <option value="">Select a config…</option>
          {configs.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </section>

      {config && (
        <section className="new-run-stage new-run-stage--2">
          {runType === 'live' ? (
            <label className="new-run-arm">
              <input type="checkbox" checked={armed} onChange={(e) => setArmed(e.target.checked)} />
              This launches a real trading process.
            </label>
          ) : (
            <p className="overview-empty">Reads historical data only.</p>
          )}

          {error && <p role="alert">{error}</p>}

          <button
            className={runType === 'live' ? 'danger-button' : undefined}
            disabled={!canStart || starting}
            onClick={handleStart}
          >
            {runType === 'live' ? 'Start' : 'Start backtest'}
          </button>
        </section>
      )}
    </div>
  )
}
