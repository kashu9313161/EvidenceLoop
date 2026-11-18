import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BUTTON, clampButton, panelPlacement, parsePosition } from '../src/chatPosition.ts'

const view = { width: 1200, height: 800 }

test('the button stays fully inside the window', () => {
  assert.deepEqual(clampButton({ x: -40, y: 5000 }, view), { x: 8, y: 800 - BUTTON - 8 })
  assert.deepEqual(clampButton({ x: 300.4, y: 200.6 }, view), { x: 300, y: 201 })
})

test('the chat opens from the button toward the middle of the window', () => {
  // Bottom right: the panel ends at the button's right and bottom edges.
  const corner = panelPlacement({ x: 1120, y: 720 }, view)
  assert.equal(corner.left + corner.width, 1120 + BUTTON)
  assert.equal(corner.top + corner.height, 720 + BUTTON)
  // Top left: the panel starts at the button.
  const topLeft = panelPlacement({ x: 40, y: 30 }, view)
  assert.deepEqual([topLeft.left, topLeft.top], [40, 30])
})

test('the chat never leaves the window', () => {
  const small = { width: 500, height: 600 }
  for (const p of [{ x: 8, y: 8 }, { x: 440, y: 540 }, { x: 250, y: 300 }]) {
    const r = panelPlacement(p, small)
    assert.ok(r.left >= 8 && r.top >= 8, JSON.stringify(r))
    assert.ok(r.left + r.width <= small.width - 8 && r.top + r.height <= small.height - 8, JSON.stringify(r))
  }
})

test('saved positions are read safely', () => {
  assert.deepEqual(parsePosition('{"x":10,"y":20}'), { x: 10, y: 20 })
  assert.equal(parsePosition(null), null)
  assert.equal(parsePosition('not json'), null)
  assert.equal(parsePosition('{"x":"a","y":2}'), null)
})
