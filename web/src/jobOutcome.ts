import type { Job } from './api'

export type JobOutcome =
  | { kind: 'running' }
  | { kind: 'open'; snapshotId: string }
  | { kind: 'failed'; message: string }

/** What the Home page should do with a job it just received, including the very first
 * response: a bad address fails before any download, so that job is already finished. */
export function jobOutcome(job: Pick<Job, 'stage' | 'snapshot_id' | 'error'>): JobOutcome {
  if (job.stage === 'error') return { kind: 'failed', message: job.error || 'The analysis failed.' }
  if (job.stage === 'done') {
    return job.snapshot_id
      ? { kind: 'open', snapshotId: job.snapshot_id }
      : { kind: 'failed', message: 'The analysis finished without a result.' }
  }
  return { kind: 'running' }
}
