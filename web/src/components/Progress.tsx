import type { Job } from '../api'

const STEPS = [
  { stage: 'fetch', label: 'Fetch the repository and check every file' },
  { stage: 'parse', label: 'Read and parse the code' },
  { stage: 'graph', label: 'Map how the files connect' },
  { stage: 'insights', label: 'Look for problems' },
]
const ORDER: Record<string, number> = { queued: -1, fetch: 0, parse: 1, measure: 1, graph: 2, insights: 3, done: 4 }

/** The analysis steps with the current one highlighted. `title` and `note` default to a first
 * analysis; the workspace passes its own when an older analysis is being brought up to date. */
export function Progress({ job, title, note }: { job: Job; title?: string; note?: string }) {
  const current = ORDER[job.stage] ?? 0
  return (
    <div className="progress" aria-live="polite">
      <h2>{title ?? `Analysing ${job.source.replace(/^https:\/\/github\.com\//, '')}`}</h2>
      <span className="faint" style={{ fontSize: 14 }}>
        {note ?? "Nothing from the repository is run. It's read and parsed only."}
      </span>
      <ol>
        {STEPS.map((step, index) => {
          const state = index < current ? 'done' : index === current ? 'active' : ''
          const counting = index === current && job.total > 0
          return (
            <li key={step.stage} className={state}>
              <span className="dot" aria-hidden="true">{index < current ? '✓' : index + 1}</span>
              <span>{step.label}</span>
              <span className="count">{counting ? `${job.done} of ${job.total}` : ''}</span>
            </li>
          )
        })}
      </ol>
      {job.message && current < 4 && <div className="detail">{job.message}</div>}
    </div>
  )
}
