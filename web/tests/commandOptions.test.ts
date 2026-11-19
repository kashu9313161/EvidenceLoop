import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { AskClaim, AskResult, SearchResult } from '../src/api.ts'
import { commandOptions, looksLikeQuestion } from '../src/commandOptions.ts'

const search: SearchResult = {
  files: ['src/data_ingestion.py'],
  symbols: [{ name: 'DataIngestion.load_data', path: 'src/data_ingestion.py', start: 11, end: 19 }],
  content: [{ relative_path: 'README.md', snippet: 'load [[data]]' }],
}

function claim(n: number): AskClaim {
  return { text: `hit ${n}`, evidence_ids: [`E${n}`], status: 'verified', reason: '', checks: [], judgment: null }
}

function lookup(count: number, more = 0): AskResult {
  return {
    id: 'a', snapshot_id: 's', question: 'q', kind: 'lookup', lookup: 'callers', route: '', status: 'answered',
    summary: '', more, provider: null, model: null, created_at: '',
    claims: Array.from({ length: count }, (_, i) => claim(i + 1)),
    evidence: Array.from({ length: count }, (_, i) => ({
      id: `E${i + 1}`, path: `f${i + 1}.py`, start: 10 + i, end: 10 + i, kind: 'code' as const, label: '', text: '', sha256: '',
    })),
    usage: { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, max_tokens: 1, max_cost_usd: 1 },
    thresholds: { support: 0.8, sufficiency: 0.7, contradiction: 0.5, calibrated: false },
  }
}

test('empty text gives no rows', () => {
  assert.deepEqual(commandOptions('  ', search, null), [])
})

test('a lookup answer comes first, capped at three, with a way to see the rest', () => {
  const rows = commandOptions('what calls load_data', search, lookup(4, 2))
  assert.deepEqual(rows.slice(0, 4).map((r) => r.kind), ['hit', 'hit', 'hit', 'more'])
  assert.equal(rows[0].path, 'f1.py')
  assert.equal(rows[0].line, 10)
  assert.equal(rows[3].title, 'See all 6 in the chat')
  assert.equal(rows.at(-1)?.kind, 'ask')  // a lookup that found something keeps Ask AI last
})

test('an open question puts Ask AI first so Enter asks', () => {
  const rows = commandOptions('how is the model trained?', search, null)
  assert.equal(rows[0].kind, 'ask')
  assert.equal(rows[0].title, 'Ask AI: “how is the model trained?”')
  assert.deepEqual(rows.slice(1).map((r) => r.kind), ['file', 'symbol', 'text'])
})

test('a name puts search results first and Ask AI last', () => {
  const rows = commandOptions('load_data', search, null)
  assert.deepEqual(rows.map((r) => r.kind), ['file', 'symbol', 'text', 'ask'])
})

test('a lookup that found nothing is treated like any other text', () => {
  const rows = commandOptions('where is the config loaded from', search, lookup(0))
  assert.equal(rows[0].kind, 'ask')
})

test('what reads like a question', () => {
  assert.equal(looksLikeQuestion('load_data'), false)
  assert.equal(looksLikeQuestion('why?'), true)
  assert.equal(looksLikeQuestion('explain the data flow'), true)
})
