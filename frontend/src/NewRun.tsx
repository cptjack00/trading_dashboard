import { useEffect, useState } from 'react'
import Modal from './Modal'

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
    <Modal
      title="Start a new run"
      onClose={onBack}
      foot={
        <>
          {error && (
            <span role="alert" className="stop-confirm">
              {error}
            </span>
          )}
          <button
            className={`btn next${runType === 'live' ? ` arm${armed ? ' ready' : ''}` : ''}`}
            disabled={!canStart || starting}
            onClick={handleStart}
          >
            {runType === 'live' ? 'Start live run' : 'Start backtest'}
          </button>
        </>
      }
    >
      <div className="field">
        <label>Project</label>
        <div className="tabs">
          {PROJECTS.map((p) => (
            <button
              key={p}
              className={`tab${project === p ? ` active ${p}` : ''}`}
              onClick={() => selectProject(p)}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>Run type</label>
        <div className="tabs">
          {(['live', 'backtest'] as RunType[]).map((t) => (
            <button
              key={t}
              className={`tab${runType === t ? ' active' : ''}`}
              onClick={() => selectRunType(t)}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label htmlFor="new-run-config">Config</label>
        <select id="new-run-config" value={config} onChange={(e) => setConfig(e.target.value)}>
          <option value="">Select a config…</option>
          {configs.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>

      {config && (
        <div className="recap">
          <span className={`r-project ${project}`}>{project}</span>
          <span className="r-config">{config}</span>
        </div>
      )}

      {config && runType === 'live' && (
        <label className="arm-toggle">
          <input type="checkbox" checked={armed} onChange={(e) => setArmed(e.target.checked)} />
          <span className="switch" />
          <span className="arm-copy">This launches a real trading process.</span>
        </label>
      )}
      {config && runType === 'backtest' && <p className="overview-empty">Reads historical data only.</p>}
    </Modal>
  )
}
