import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Report } from '../src/api.ts'
import { computeLayout, fileGraph, defaultSettings, layoutFor, miniMap } from '../src/mapLayout.ts'

function node(id: string, fanIn: number, extra: Record<string, unknown> = {}) {
  return {
    id, name: id, area: id.split('/')[0], role: 'module', loc: 10, fan_in: fanIn, fan_out: 0,
    importance: 0, in_cycle: false, status: 'analyzed', is_test: false, is_entry: false,
    severity: null, insights: 0, ...extra,
  }
}

function report(nodes: ReturnType<typeof node>[], edges: [string, string, Record<string, unknown>?][], id = 'r1') {
  const areas = [...new Set(nodes.map((n) => n.area))].map((name) => ({
    name, files: nodes.filter((n) => n.area === name).map((n) => n.id),
  }))
  return {
    version: '2.3',
    snapshot: { id },
    architecture: { areas },
    graph: {
      nodes,
      edges: edges.map(([source, target, extra]) => ({
        source, target, tier: 'confirmed', kind: 'imports', type_only: false, lines: [1], ...extra,
      })),
      area_edges: [],
    },
  } as unknown as Report
}

const small = () => report(
  [node('app/main.py', 0), node('app/db.py', 3), node('app/models.py', 2), node('app/util.py', 1),
    node('app/cli.py', 0), node('tests/test_db.py', 0, { is_test: true })],
  [['app/main.py', 'app/db.py'], ['app/cli.py', 'app/db.py'], ['app/models.py', 'app/db.py'],
    ['app/main.py', 'app/models.py'], ['app/cli.py', 'app/models.py'], ['app/main.py', 'app/util.py'],
    ['tests/test_db.py', 'app/db.py', { kind: 'tests' }]],
)

test('the preview labels only the most-used files and leaves tests out', () => {
  const model = miniMap(small())
  assert.equal(model.view, 'files')
  assert.deepEqual(model.nodes.filter((n) => n.labelled).map((n) => n.id),
    ['app/db.py', 'app/models.py', 'app/util.py'])
  assert.equal(model.files, 5)
  assert.equal(model.connections, 6)
})

test('the preview and the full map share one layout', () => {
  const r = small()
  const model = miniMap(r)
  const full = layoutFor(r, 'files')
  for (const n of model.nodes) assert.deepEqual({ x: n.x, y: n.y }, full.get(n.id))
})

test('the layout is the same every time it is computed', () => {
  const r = small()
  const { nodes, edges } = fileGraph(r, defaultSettings(r))
  const areas = ['app', 'tests']
  assert.deepEqual([...computeLayout(nodes, edges, areas, 'files')], [...computeLayout(nodes, edges, areas, 'files')])
})

test('type-only imports are not drawn', () => {
  const r = report([node('a/x.ts', 1), node('a/y.ts', 0)], [['a/y.ts', 'a/x.ts', { type_only: true }]])
  assert.equal(miniMap(r).connections, 0)
})

test('a large repository is previewed by area', () => {
  const nodes = Array.from({ length: 160 }, (_, i) => node(`${['core', 'web', 'cli'][i % 3]}/f${i}.py`, 0))
  const model = miniMap(report(nodes, [], 'big'))
  assert.equal(model.view, 'areas')
  assert.deepEqual(model.nodes.map((n) => n.id).sort(), ['cli', 'core', 'web'])
  assert.equal(model.files, 160)
})
