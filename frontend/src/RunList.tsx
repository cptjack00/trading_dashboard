import { useEffect, useState } from 'react'

export type RunStatus = 'live' | 'stopped' | 'crashed' | 'backtest'

export type Run = {
  run_id: string
  project: string
  run_type: string
  status: RunStatus
  started_at: number
  ended_at: number | null
  pnl: number
}

const POLL_MS = 5000

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':')
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString()
}

function RunRow({ run, now, onSelect }: { run: Run; now: number; onSelect: (run: Run) => void }) {
  const isBacktest = run.status === 'backtest'
  const end = run.ended_at ?? now
  const duration = end - run.started_at

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(run)
    }
  }

  return (
    <li
      className={`run-row run-row--${run.status}`}
      onClick={() => onSelect(run)}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
    >
      <span className={`pulse pulse--${run.status === 'live' ? 'live' : 'dead'}`} aria-hidden="true" />
      <span className="run-project">{run.project}</span>
      <span className={`run-badge run-badge--${run.status}`}>{run.status.toUpperCase()}</span>
      <span className="run-id">{run.run_id}</span>
      {isBacktest ? (
        <span className="run-duration">
          {formatDate(run.started_at)} → {run.ended_at ? formatDate(run.ended_at) : 'in progress'}
        </span>
      ) : (
        <span className="run-duration">{formatDuration(duration)}</span>
      )}
      <span className={`run-pnl ${run.pnl >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}`}>
        {run.pnl >= 0 ? '+' : ''}
        {run.pnl.toFixed(2)}
      </span>
    </li>
  )
}

export default function RunList({ onSelectRun }: { onSelectRun: (run: Run) => void }) {
  const [runs, setRuns] = useState<Run[]>([])
  const [now, setNow] = useState(() => Date.now() / 1000)

  useEffect(() => {
    let cancelled = false
    async function load() {
      const res = await fetch('/api/runs')
      if (!res.ok || cancelled) return
      const data: Run[] = await res.json()
      if (!cancelled) setRuns(data)
    }
    load()
    const poll = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(poll)
    }
  }, [])

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(tick)
  }, [])

  if (runs.length === 0) {
    return <p>No runs yet.</p>
  }

  return (
    <ul className="run-list">
      {runs.map((run) => (
        <RunRow key={`${run.project}-${run.run_id}`} run={run} now={now} onSelect={onSelectRun} />
      ))}
    </ul>
  )
}
