import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { SourceRules } from '../src/api.ts'
import { pickFolder, skipReason } from '../src/sourceRules.ts'
import { formatBytes } from '../src/util.ts'

// The shape GET /api/config sends (a subset of the real lists).
const RULES: SourceRules = {
  supported_extensions: ['.py', '.ts', '.md', '.json', '.svg'].filter((e) => e !== '.svg'),
  ignored_directories: {
    'Virtual environment': ['venv', '.venv', 'site-packages'],
    'Installed dependencies': ['node_modules'],
  },
  vendor_directory: 'vendor',
  vendor_extensions: ['.js', '.ts', '.go'],
  skipped_extensions: { Dataset: ['.csv'], 'Model weights': ['.pt'], 'Media or document': ['.png'] },
  ignored_file_names: ['.gitmodules'],
  svg_skip_bytes: 100 * 1024,
  max_file_bytes: 1024 * 1024,
}

test('the browser skips the same files as the server', () => {
  assert.equal(skipReason('app/main.py', 10, RULES), null)
  assert.equal(skipReason('a/venv/lib/x.py', 10, RULES), 'Virtual environment')
  assert.equal(skipReason('web/node_modules/react/index.ts', 10, RULES), 'Installed dependencies')
  assert.equal(skipReason('web/vendor/lib.ts', 10, RULES), 'Installed dependencies')
  assert.equal(skipReason('pkg/vendor/lib.py', 10, RULES), null)
  assert.equal(skipReason('data/train.csv', 10, RULES), 'Dataset')
  assert.equal(skipReason('best.pt', 10, RULES), 'Model weights')
  assert.equal(skipReason('big.svg', 200 * 1024, RULES), 'Media or document')
  assert.equal(skipReason('Makefile', 10, RULES), 'Unsupported file type')
  assert.equal(skipReason('bundle.ts', 2 * 1024 * 1024, RULES), 'Too large')
})

test('a picked folder keeps only source files, with paths under the folder name', () => {
  const files = [
    { webkitRelativePath: 'proj/app.py', name: 'app.py', size: 5 },
    { webkitRelativePath: 'proj/README.md', name: 'README.md', size: 5 },
    { webkitRelativePath: 'proj/data/a.csv', name: 'a.csv', size: 5e8 },
    { webkitRelativePath: 'proj/data/b.csv', name: 'b.csv', size: 5 },
    { webkitRelativePath: 'proj/.venv/x.py', name: 'x.py', size: 5 },
  ]
  const picked = pickFolder(files, RULES)
  assert.equal(picked.name, 'proj')
  assert.deepEqual(picked.keep.map((k) => k.path), ['proj/app.py', 'proj/README.md'])
  assert.equal(picked.skipped, 3)
  assert.deepEqual(picked.reasons, [
    { reason: 'Dataset', files: 2 }, { reason: 'Virtual environment', files: 1 }])
})

test('byte sizes read naturally', () => {
  assert.equal(formatBytes(512), '512 bytes')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(1024 * 1024), '1 MB')
  assert.equal(formatBytes(1.45e9), '1.4 GB')
})
