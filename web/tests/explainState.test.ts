import assert from 'node:assert/strict'
import { test } from 'node:test'
import { explainStep, firstSentence } from '../src/explainState.ts'

const explanation = {
  summary: 'Loads data.', purpose: 'Loads data.', inputs: '', steps: [], outputs: '', why: '',
  dropped: 0, model: 'm', sent: 'Sent: code.', created_at: 'now',
}

test('without an API key the facts stand alone', () => {
  assert.equal(explainStep({ available: false, reason: 'Set ANTHROPIC_API_KEY' }, true), 'facts')
})

test('a cached explanation is shown', () => {
  assert.equal(explainStep({ available: true, status: 'ready', explanation }, false), 'show')
})

test('a missing one is written only where the view may spend', () => {
  assert.equal(explainStep({ available: true, status: 'missing', explanation: null }, true), 'write')
  assert.equal(explainStep({ available: true, status: 'missing', explanation: null }, false), 'facts')
})

test('a file that cannot be explained falls back to the facts', () => {
  assert.equal(explainStep({ available: true, status: 'unavailable', reason: 'Not code' }, true), 'facts')
})

test('first sentence', () => {
  assert.equal(firstSentence('Loads the CSV. Three scripts use it.'), 'Loads the CSV.')
  assert.equal(firstSentence('Uses v1.2 of the API. Then more.'), 'Uses v1.2 of the API.')
  assert.equal(firstSentence('No full stop'), 'No full stop')
})
