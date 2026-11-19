import assert from 'node:assert/strict'
import { test } from 'node:test'
import { scrollToLine, type Scroller } from '../src/scrollToLine.ts'

/** A stand-in ResizeObserver the test can fire by hand. */
function fakeObserver() {
  const state = { fire: () => {}, watching: false }
  class Fake {
    constructor(callback: () => void) { state.fire = callback }
    observe() { state.watching = true }
    disconnect() { state.watching = false }
  }
  return { Fake, state }
}

test('the line lands a third of the way down the scroller', () => {
  const scroller: Scroller = { clientHeight: 900, scrollTop: 0 }
  scrollToLine({ offsetTop: 20000 }, scroller, 1500, undefined)
  assert.equal(scroller.scrollTop, 19700)
})

test('the line stays in view when a card above the code shrinks the scroller', () => {
  const scroller: Scroller = { clientHeight: 840, scrollTop: 0 }
  const { Fake, state } = fakeObserver()
  const stop = scrollToLine({ offsetTop: 20000 }, scroller, 1500, Fake)
  scroller.clientHeight = 340 // the function card opened
  state.fire()
  assert.equal(scroller.scrollTop, Math.max(0, 20000 - 340 / 3))
  stop()
  assert.equal(state.watching, false)
})

test('it stops watching once the layout has settled', async () => {
  const { Fake, state } = fakeObserver()
  scrollToLine({ offsetTop: 10 }, { clientHeight: 100, scrollTop: 0 }, 5, Fake)
  assert.equal(state.watching, true)
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(state.watching, false)
})
