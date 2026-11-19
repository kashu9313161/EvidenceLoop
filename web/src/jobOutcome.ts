import type { IngestPlan, Job } from './api'

export type JobOutcome =
  | { kind: 'running' }
  | { kind: 'scope'; plan: IngestPlan; source: string }
  | { kind: 'open'; snapshotId: string }
  | { kind: 'failed'; message: string }

/** What the Home page should do with a job it just received, including the very first
 * response: a bad address fails before any download, so that job is already finished. */
export function jobOutcome(
  job: Pick<Job, 'stage' | 'snapshot_id' | 'error'> & Partial<Pick<Job, 'plan' | 'source'>>,
): JobOutcome {
  // Too much source to analyse at once: the person picks a folder (or the most important files).
  if (job.stage === 'needs_scope') {
    return job.plan
      ? { kind: 'scope', plan: job.plan, source: job.source ?? '' }
      : { kind: 'failed', message: 'This repository is too big to analyse at once.' }
  }
  if (job.stage === 'error') return { kind: 'failed', message: job.error || 'The analysis failed.' }
  if (job.stage === 'done') {
    return job.snapshot_id
      ? { kind: 'open', snapshotId: job.snapshot_id }
      : { kind: 'failed', message: 'The analysis finished without a result.' }
  }
  return { kind: 'running' }
}
