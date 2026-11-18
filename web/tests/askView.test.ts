import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { AskClaim, AskResult } from '../src/api.ts'
import { citedPaths, statusCounts, usageLine } from '../src/askView.ts'

function claim(status: AskClaim['status'], ids: string[]): AskClaim {
  return { text: 't', evidence_ids: ids, status, reason: '', checks: [], judgment: null }
}

function result(over: Partial<AskResult>): AskResult {
  return {
    id: 'a', snapshot_id: 's', question: 'q', kind: 'analytical', lookup: null, route: '', status: 'answered',
    summary: '', more: 0, claims: [], provider: 'gemini', model: 'gemini-3.5-flash-lite',
    evidence: [
      { id: 'E1', path: 'a.py', start: 1, end: 2, kind: 'code', label: '', text: '', sha256: '' },
      { id: 'E2', path: 'b.py', start: 1, end: 2, kind: 'code', label: '', text: '', sha256: '' },
      { id: 'E3', path: 'a.py', start: 5, end: 6, kind: 'code', label: '', text: '', sha256: '' },
    ],
    usage: { calls: 3, input_tokens: 12345, output_tokens: 820, cost_usd: 0.0058, max_tokens: 60000, max_cost_usd: 0.05 },
    thresholds: { support: 0.8, sufficiency: 0.7, contradiction: 0.5, calibrated: false },
    created_at: '', ...over,
  }
}

test('show in map uses files cited by claims that passed their checks', () => {
  const r = result({ claims: [claim('verified', ['E1', 'E3']), claim('insufficient', ['E2']), claim('rejected', ['E2', 'E9'])] })
  assert.deepEqual(citedPaths(r), ['a.py', 'b.py'])
  assert.deepEqual(citedPaths(r, r.claims[2]), ['b.py'])  // one claim: its own files, ignoring unknown ids
})

test('usage line', () => {
  assert.equal(usageLine(result({})), '3 AI calls, 12,345 tokens in, 820 out, about $0.0058 at list price.')
  assert.equal(usageLine(result({ kind: 'lookup', usage: { ...result({}).usage, calls: 0 } })),
    'Answered from the index. No AI was used.')
  assert.match(usageLine(result({ usage: { ...result({}).usage, cost_usd: null } })), /cost unknown/)
})

test('status counts', () => {
  assert.equal(statusCounts([claim('rejected', []), claim('verified', []), claim('verified', [])]), '2 verified, 1 rejected')
})
