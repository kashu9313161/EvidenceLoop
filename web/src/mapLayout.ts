/** The dependency map's layout, shared by the Overview preview and the full Map so both show
 * the same shape. The layout is computed once per repository and view, deterministically
 * (seeded positions and a seeded random source), so it is identical even when recomputed. */
import {
  forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum,
} from 'd3-force'
import type { Area, GraphEdge, GraphNode, Report } from './api'

export const MAX_FILE_NODES = 400
export const MAX_EDGES = 1500
/** Above this many files the Overview preview shows areas instead of files. */
export const MINI_AREAS_ABOVE = 150

export type MapViewKind = 'files' | 'areas'

export interface MapSettings {
  showTests: boolean
  showInferred: boolean
  showOrphans: boolean
}

export interface Point {
  x: number
  y: number
}

export function hash(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0) / 4294967295
}

export function defaultSettings(report: Report): MapSettings {
  return { showTests: false, showInferred: true, showOrphans: report.graph.nodes.length <= MAX_FILE_NODES / 2 }
}

export function startsWithAreas(report: Report): boolean {
  return report.graph.nodes.length > MAX_FILE_NODES || report.graph.edges.length > MAX_EDGES
}

/** The files and imports the map draws with these settings. Files in `keep` (the selected
 * file, files an answer cites) are drawn even when the settings would hide them. */
export function fileGraph(report: Report, settings: MapSettings, keep: Iterable<string | null> = []):
  { nodes: GraphNode[]; edges: GraphEdge[] } {
  const kept = new Set([...keep].filter((p): p is string => !!p))
  // Type-hint-only imports never run, so they aren't dependencies (and make no cycles).
  const edges = report.graph.edges.filter(
    (e) => !e.type_only && (settings.showInferred || e.tier === 'confirmed') &&
      (settings.showTests || e.kind !== 'tests'),
  )
  const connected = new Set(edges.flatMap((e) => [e.source, e.target]))
  const nodes = report.graph.nodes
    // Empty files (package markers like an empty __init__.py) carry no information on a map.
    .filter((n) => kept.has(n.id) || ((n.loc > 0 || connected.has(n.id)) &&
      (settings.showTests || !n.is_test) && (settings.showOrphans || connected.has(n.id))))
  const ids = new Set(nodes.map((n) => n.id))
  return { nodes, edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)) }
}

/** The areas and the imports between them. */
export function areaGraph(report: Report, showTests: boolean):
  { nodes: Area[]; edges: { source: string; target: string; edges: number }[] } {
  const nodes = report.architecture.areas.filter((a) => showTests || a.name !== 'tests')
  const ids = new Set(nodes.map((a) => a.name))
  return { nodes, edges: report.graph.area_edges.filter((e) => ids.has(e.source) && ids.has(e.target)) }
}

/** Where a node starts before any layout: around a circle, one sector per area. */
export function seedPosition(id: string, area: string, areas: string[], isArea: boolean): Point {
  const index = Math.max(areas.indexOf(area), 0)
  const angle = (index / Math.max(areas.length, 1)) * Math.PI * 2
  if (isArea) return { x: Math.cos(angle) * 120, y: Math.sin(angle) * 120 }
  const r = 140 + hash(id) * 90
  return {
    x: Math.cos(angle) * r + (hash(`${id}x`) - 0.5) * 60,
    y: Math.sin(angle) * r + (hash(`${id}y`) - 0.5) * 60,
  }
}

interface SimNode extends SimulationNodeDatum {
  id: string
}

function seededRandom(seed: number): () => number {
  let state = seed >>> 0 || 1
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0
    return state / 4294967296
  }
}

/** Run the force layout to rest and return each node's position. */
export function computeLayout(
  nodes: { id: string; area: string }[],
  links: { source: string; target: string }[],
  areas: string[],
  view: MapViewKind,
): Map<string, Point> {
  const simNodes: SimNode[] = nodes.map((n) => ({ id: n.id, ...seedPosition(n.id, n.area, areas, view === 'areas') }))
  const simLinks = links.map((l) => ({ source: l.source, target: l.target }))
  const small = nodes.length < 80
  const gravity = view === 'areas' ? 0.04 : 0.07
  const simulation = forceSimulation<SimNode>(simNodes)
    .randomSource(seededRandom(nodes.length + 7))
    .force('charge', forceManyBody<SimNode>().strength(view === 'areas' ? -900 : small ? -260 : -140))
    .force('link', forceLink<SimNode, { source: string | SimNode; target: string | SimNode }>(simLinks)
      .id((n) => n.id)
      .distance(view === 'areas' ? 170 : small ? 70 : 46))
    .force('x', forceX<SimNode>(0).strength(gravity))
    .force('y', forceY<SimNode>(0).strength(gravity))
    .stop()
  simulation.tick(300)
  return new Map(simNodes.map((n) => [n.id, { x: n.x ?? 0, y: n.y ?? 0 }]))
}

const cache = new Map<string, Map<string, Point>>()

/** The layout of the map as it first opens (default settings), cached per repository and view. */
export function layoutFor(report: Report, view: MapViewKind): Map<string, Point> {
  const key = `${report.snapshot.id}|${report.version}|${view}`
  const known = cache.get(key)
  if (known) return known
  const areaNames = report.architecture.areas.map((a) => a.name)
  const settings = defaultSettings(report)
  let layout: Map<string, Point>
  if (view === 'areas') {
    const { nodes, edges } = areaGraph(report, settings.showTests)
    layout = computeLayout(nodes.map((a) => ({ id: a.name, area: a.name })), edges, areaNames, 'areas')
  } else {
    const { nodes, edges } = fileGraph(report, settings)
    layout = computeLayout(nodes, edges, areaNames, 'files')
  }
  cache.set(key, layout)
  return layout
}

export interface MiniNode {
  id: string
  label: string
  area: string
  x: number
  y: number
  r: number
  labelled: boolean
}

export interface MiniMapModel {
  view: MapViewKind
  nodes: MiniNode[]
  links: { source: string; target: string }[]
  files: number
  connections: number
}

const shortName = (path: string) => path.split('/').pop() ?? path

/** What the Overview preview draws: the full map's opening view, with only the two or three
 * most-used files (or largest areas) labelled. */
export function miniMap(report: Report): MiniMapModel {
  const settings = defaultSettings(report)
  if (report.graph.nodes.length > MINI_AREAS_ABOVE) {
    const { nodes, edges } = areaGraph(report, settings.showTests)
    const layout = layoutFor(report, 'areas')
    const labelled = new Set([...nodes].sort((a, b) => b.files.length - a.files.length).slice(0, 3).map((a) => a.name))
    return {
      view: 'areas',
      nodes: nodes.map((a) => ({
        id: a.name, label: a.name, area: a.name, ...layout.get(a.name)!,
        r: 12 + Math.sqrt(a.files.length) * 5, labelled: labelled.has(a.name),
      })),
      links: edges.map((e) => ({ source: e.source, target: e.target })),
      files: nodes.reduce((sum, a) => sum + a.files.length, 0),
      connections: edges.reduce((sum, e) => sum + e.edges, 0),
    }
  }
  const { nodes, edges } = fileGraph(report, settings)
  const layout = layoutFor(report, 'files')
  const labelled = new Set(
    [...nodes].filter((n) => n.fan_in > 0).sort((a, b) => b.fan_in - a.fan_in || a.id.localeCompare(b.id))
      .slice(0, 3).map((n) => n.id),
  )
  return {
    view: 'files',
    nodes: nodes.map((n) => ({
      id: n.id, label: shortName(n.id), area: n.area, ...layout.get(n.id)!,
      r: 5 + Math.sqrt(n.fan_in) * 3.6, labelled: labelled.has(n.id),
    })),
    links: edges.map((e) => ({ source: e.source, target: e.target })),
    files: nodes.length,
    connections: edges.length,
  }
}
