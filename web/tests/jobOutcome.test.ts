import assert from 'node:assert/strict'
import { test } from 'node:test'
import { jobOutcome } from '../src/jobOutcome.ts'

test('a job that already failed in the first response shows its error', () => {
  const job = { stage: 'error', snapshot_id: null, error: 'Expected exactly: https://github.com/owner/repository' }
  assert.deepEqual(jobOutcome(job), {
    kind: 'failed',
    message: 'Expected exactly: https://github.com/owner/repository',
  })
})

test('a failed job without a message still says something', () => {
  assert.deepEqual(jobOutcome({ stage: 'error', snapshot_id: null, error: null }), {
    kind: 'failed',
    message: 'The analysis failed.',
  })
})

test('a finished job opens its workspace', () => {
  assert.deepEqual(jobOutcome({ stage: 'done', snapshot_id: 'github:a/b@1', error: null }), {
    kind: 'open',
    snapshotId: 'github:a/b@1',
  })
})

test('a job in progress keeps polling', () => {
  for (const stage of ['queued', 'fetch', 'parse', 'graph']) {
    assert.deepEqual(jobOutcome({ stage, snapshot_id: null, error: null }), { kind: 'running' })
  }
})
