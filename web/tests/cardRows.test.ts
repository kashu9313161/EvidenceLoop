import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Explanation, FileFacts, FunctionMetric } from '../src/api.ts'
import { cardFor, cardStep, fileRows, paramLabel } from '../src/cardRows.ts'

const facts: FileFacts = {
  purpose: null,
  inputs: { text: 'It uses pricing.py.', source: 'code' },
  steps: [{ symbol: 'place_order', kind: 'function', start: 3, end: 4, does: '', source: 'code' }],
  outputs: { text: 'Other files import place_order.', source: 'code', names: [] },
  why: { text: 'api.py depends on it.', source: 'code' },
}

test('without AI the file rows come from the code, and a missing purpose is left out', () => {
  const rows = fileRows(undefined, facts)
  assert.deepEqual(rows.map((r) => [r.key, r.source]), [
    ['inputs', 'code'], ['steps', 'code'], ['outputs', 'code'], ['why', 'code'],
  ])
})

test('the AI answer replaces the code rows it covers', () => {
  const explanation: Explanation = {
    summary: 'Places orders.', purpose: 'Places orders.', inputs: '', outputs: 'The order total.',
    why: '', dropped: 0, model: 'm', sent: 'Sent.', created_at: 'now',
    steps: [{ symbol: 'place_order', kind: 'function', start: 3, end: 4, does: 'Totals.', source: 'ai-checked' }],
  }
  const rows = fileRows(explanation, facts)
  assert.deepEqual(rows.map((r) => [r.key, r.source]), [
    ['purpose', 'ai'], ['inputs', 'code'], ['steps', 'ai-checked'], ['outputs', 'ai'], ['why', 'code'],
  ])
})

test('a card is written only where the view may spend', () => {
  const card = {} as never
  assert.equal(cardStep({ available: true, status: 'missing', card }, true), 'write')
  assert.equal(cardStep({ available: true, status: 'missing', card }, false), 'show')
  assert.equal(cardStep({ available: false, status: 'off', card }, true), 'show')
  assert.equal(cardStep({ available: true, status: 'unavailable', reason: 'No.' }, true), 'unavailable')
})

test('parameter labels show * and **', () => {
  assert.equal(paramLabel({ name: 'args', kind: 'var_positional' }), '*args')
  assert.equal(paramLabel({ name: 'kw', kind: 'var_keyword' }), '**kw')
  assert.equal(paramLabel({ name: 'x', kind: 'keyword_only' }), 'x')
})

test('the card follows the opened line', () => {
  const fn = (id: string, start: number, end: number): FunctionMetric =>
    ({ name: id, start, end, complexity: 1, lines: end - start + 1, nesting: 0, id, public: true, callers: 0, callees: 0 })
  const metrics = [fn('a', 1, 10), fn('b', 12, 20)]
  assert.equal(cardFor(metrics, 12, null), 'b')
  assert.equal(cardFor(metrics, 15, 'b'), 'b')  // a step inside the shown function
  assert.equal(cardFor(metrics, 15, null), null)  // a plain line opens no card
  assert.equal(cardFor(metrics, 5, 'b'), null)
  assert.equal(cardFor(metrics, null, 'b'), null)
})
