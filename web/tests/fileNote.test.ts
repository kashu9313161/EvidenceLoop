import assert from 'node:assert/strict'
import { test } from 'node:test'
import { plainFileNote } from '../src/fileNote.ts'

test('an indexed text file says it is searchable but not analysed', () => {
  assert.match(plainFileNote('indexed', null), /shown as text, and included in search/)
})

test('a skipped file gives the reason in a sentence', () => {
  assert.equal(plainFileNote('skipped', 'Unsupported file type'), 'Not analysed: unsupported file type.')
  assert.equal(plainFileNote('skipped', null), 'Not analysed: unsupported file.')
})

test('a failed file says why', () => {
  assert.equal(plainFileNote('failed', 'Syntax error at line 2'), "Couldn't be analysed: Syntax error at line 2.")
})

test('analysed code needs no note', () => {
  assert.equal(plainFileNote('analyzed', null), '')
})
