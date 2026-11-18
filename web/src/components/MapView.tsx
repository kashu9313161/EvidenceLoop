import { forceX, forceY } from 'd3-force'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from 'react-force-graph-2d'
import type { Report } from '../api'
import { areaLabel, areaPalette, cssVar, fileName, plural, useTheme } from '../util'

interface Props {
  report: Report
  selected: string | null
  onSelect: (path: string | null) => void
  impactOn: boolean
}

type ColourBy = 'area' | 'issues'
type View = 'files' | 'areas'

interface MapNode {
  id: string
  label: string
  area: string
  weight: number
  fanIn: number
  failed: boolean
  cycle: boolean
  test: boolean
  entry: boolean
  severity: string | null
  isArea?: boolean
  x?: number
  y?: number
}

interface MapLink {
  source: string | MapNode
  target: string | MapNode
  tier: string
  kind: string
  weight: number
}

const MAX_FILE_NODES = 400
const MAX_EDGES = 1500

function hash(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0) / 4294967295
}

const idOf = (end: string | MapNode) => (typeof end === 'string' ? end : end.id)

export function MapView({ report, selected, onSelect, impactOn }: Props) {
  const [theme] = useTheme()
  const wrap = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<ForceGraphMethods<NodeObject<MapNode>, LinkObject<MapNode, MapLink>> | undefined>(undefined)
  const [size, setSize] = useState({ width: 800, height: 600 })
  const tooBig = report.graph.nodes.length > MAX_FILE_NODES || report.graph.edges.length > MAX_EDGES
  const [view, setView] = useState<View>(tooBig ? 'areas' : 'files')
  const [colourBy, setColourBy] = useState<ColourBy>('area')
  const [showTests, setShowTests] = useState(false)
  const [showInferred, setShowInferred] = useState(true)
  const [showOrphans, setShowOrphans] = useState(report.graph.nodes.length <= MAX_FILE_NODES / 2)
  const [hover, setHover] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [areaFocus, setAreaFocus] = useState<string | null>(null)

  const colours = useMemo(() => areaPalette(report.architecture.areas, theme), [report, theme])
  const ink = useMemo(() => ({
    paper: cssVar('--paper'), paper2: cssVar('--paper-2'), paper3: cssVar('--paper-3'),
    line: cssVar('--line'), signal: cssVar('--signal'), amber: cssVar('--amber'), low: cssVar('--low'),
    ink: cssVar('--ink'), rule: cssVar('--rule-strong'),
  }), [theme]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // ---- data ----------------------------------------------------------------------------
  const data = useMemo(() => {
    const areas = report.architecture.areas
    const areaIndex = new Map(areas.map((a, i) => [a.name, i]))
    const angleOf = (area: string) => ((areaIndex.get(area) ?? 0) / Math.max(areas.length, 1)) * Math.PI * 2

    if (view === 'areas') {
      const nodes: MapNode[] = areas
        .filter((a) => showTests || a.name !== 'tests')
        .map((a) => ({
          id: a.name, label: areaLabel(a.name), area: a.name, weight: a.files.length,
          fanIn: report.graph.area_edges.filter((e) => e.target === a.name).reduce((s, e) => s + e.edges, 0),
          failed: false, cycle: false, test: a.name === 'tests', entry: false,
          severity: a.insights ? 'medium' : null, isArea: true,
          x: Math.cos(angleOf(a.name)) * 120, y: Math.sin(angleOf(a.name)) * 120,
        }))
      const ids = new Set(nodes.map((n) => n.id))
      const links: MapLink[] = report.graph.area_edges
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({ source: e.source, target: e.target, tier: 'confirmed', kind: 'imports', weight: e.edges }))
      return { nodes, links }
    }

    // Type-hint-only imports never run, so they aren't dependencies (and make no cycles).
    const edges = report.graph.edges.filter(
      (e) => !e.type_only && (showInferred || e.tier === 'confirmed') && (showTests || e.kind !== 'tests'),
    )
    const connected = new Set(edges.flatMap((e) => [e.source, e.target]))
    const nodes: MapNode[] = report.graph.nodes
      // Empty files (package markers like an empty __init__.py) carry no information on a map.
      .filter((n) => n.loc > 0 || connected.has(n.id) || n.id === selected)
      .filter((n) => (showTests || !n.is_test) && (showOrphans || connected.has(n.id) || n.id === selected))
      .map((n) => {
        const angle = angleOf(n.area)
        const r = 140 + hash(n.id) * 90
        return {
          id: n.id, label: fileName(n.id), area: n.area, weight: n.loc, fanIn: n.fan_in,
          failed: n.status === 'failed', cycle: n.in_cycle, test: n.is_test, entry: n.is_entry,
          severity: n.severity,
          x: Math.cos(angle) * r + (hash(`${n.id}x`) - 0.5) * 60,
          y: Math.sin(angle) * r + (hash(`${n.id}y`) - 0.5) * 60,
        }
      })
    const ids = new Set(nodes.map((n) => n.id))
    const links: MapLink[] = edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, tier: e.tier, kind: e.kind, weight: e.lines.length }))
    return { nodes, links }
  }, [report, view, showTests, showInferred, showOrphans, selected])

  // adjacency for hover / impact
  const { neighbours, predecessors } = useMemo(() => {
    const nb = new Map<string, Set<string>>()
    const pred = new Map<string, Set<string>>()
    for (const link of data.links) {
      const s = idOf(link.source)
      const t = idOf(link.target)
      if (!nb.has(s)) nb.set(s, new Set())
      if (!nb.has(t)) nb.set(t, new Set())
      nb.get(s)!.add(t)
      nb.get(t)!.add(s)
      if (!pred.has(t)) pred.set(t, new Set())
      pred.get(t)!.add(s)
    }
    return { neighbours: nb, predecessors: pred }
  }, [data])

  const impactSet = useMemo(() => {
    if (!impactOn || !selected || view !== 'files') return null
    const seen = new Set<string>([selected])
    const stack = [selected]
    while (stack.length) {
      const node = stack.pop()!
      for (const p of predecessors.get(node) ?? []) {
        if (!seen.has(p)) {
          seen.add(p)
          stack.push(p)
        }
      }
    }
    return seen
  }, [impactOn, selected, predecessors, view])

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return null
    return new Set(data.nodes.filter((n) => n.id.toLowerCase().includes(q)).map((n) => n.id))
  }, [query, data])

  const focusId = hover ?? (view === 'files' ? selected : null)
  const isLit = useCallback((id: string) => {
    if (impactSet) return impactSet.has(id)
    if (matches) return matches.has(id)
    if (areaFocus && view === 'files') {
      const node = data.nodes.find((n) => n.id === id)
      return node?.area === areaFocus
    }
    if (!focusId) return true
    return id === focusId || (neighbours.get(focusId)?.has(id) ?? false)
  }, [impactSet, matches, focusId, neighbours, areaFocus, view, data])

  // ---- forces & framing ------------------------------------------------------------------
  useEffect(() => {
    const fg = graphRef.current
    if (!fg) return
    const small = data.nodes.length < 80
    const charge = fg.d3Force('charge') as { strength?: (v: number) => void } | undefined
    charge?.strength?.(view === 'areas' ? -900 : small ? -260 : -140)
    const link = fg.d3Force('link') as { distance?: (v: number) => void } | undefined
    link?.distance?.(view === 'areas' ? 170 : small ? 70 : 46)
    // Gentle gravity keeps unconnected files and small clusters on screen.
    fg.d3Force('x', forceX(0).strength(view === 'areas' ? 0.04 : 0.07))
    fg.d3Force('y', forceY(0).strength(view === 'areas' ? 0.04 : 0.07))
    fg.d3ReheatSimulation()
  }, [view, data])

  const framed = useRef(false)
  useEffect(() => { framed.current = false }, [view, showTests, showOrphans])

  useEffect(() => {
    if (!selected || view !== 'files') return
    const node = data.nodes.find((n) => n.id === selected)
    if (node?.x != null && node.y != null) graphRef.current?.centerAt(node.x, node.y, 500)
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps

  const reduceMotion = typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  // ---- drawing -----------------------------------------------------------------------------
  const radius = (n: MapNode) => (n.isArea ? 12 + Math.sqrt(n.weight) * 5 : 5 + Math.sqrt(n.fanIn) * 3.6)

  const nodeColour = (n: MapNode) => {
    if (n.failed) return ink.paper3
    if (colourBy === 'issues') {
      if (n.severity === 'high') return ink.signal
      if (n.severity === 'medium') return ink.amber
      if (n.severity === 'low') return ink.low
      return ink.paper3
    }
    return colours[n.area] ?? ink.line
  }

  const drawNode = (node: NodeObject<MapNode>, ctx: CanvasRenderingContext2D, scale: number) => {
    const n = node as MapNode
    const r = radius(n)
    const lit = isLit(n.id)
    const isSel = n.id === selected && view === 'files'
    ctx.globalAlpha = lit ? 1 : 0.13
    ctx.beginPath()
    ctx.arc(n.x!, n.y!, r, 0, Math.PI * 2)
    ctx.fillStyle = nodeColour(n)
    ctx.fill()
    if (n.failed) {
      ctx.lineWidth = 1.2 / scale
      ctx.strokeStyle = ink.paper2
      ctx.setLineDash([2 / scale, 2 / scale])
      ctx.stroke()
      ctx.setLineDash([])
    }
    if (n.cycle) {
      ctx.beginPath()
      ctx.arc(n.x!, n.y!, r + 2.6, 0, Math.PI * 2)
      ctx.lineWidth = 1.8
      ctx.strokeStyle = ink.signal
      ctx.stroke()
    }
    if (isSel) {
      ctx.beginPath()
      ctx.arc(n.x!, n.y!, r + (n.cycle ? 5.5 : 3.2), 0, Math.PI * 2)
      ctx.lineWidth = 2
      ctx.strokeStyle = ink.paper
      ctx.stroke()
    }
    const important = n.isArea || n.fanIn >= 3 || n.entry
    const near = focusId && (n.id === focusId || neighbours.get(focusId)?.has(n.id))
    const showLabel = data.nodes.length <= 40 || scale > 1.6 || important || near || isSel ||
      (matches?.has(n.id) ?? false)
    if (showLabel && (lit || isSel)) {
      const size = (n.isArea ? 14 : important ? 12.5 : 11.5) / Math.max(Math.sqrt(scale), 0.8)
      ctx.font = `${important || isSel ? 600 : 400} ${Math.max(size, 3)}px "IBM Plex Sans", sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.lineWidth = 3 / scale
      ctx.strokeStyle = ink.ink
      ctx.strokeText(n.label, n.x!, n.y! + r + 3)
      ctx.fillStyle = isSel || near ? ink.paper : ink.paper2
      ctx.fillText(n.label, n.x!, n.y! + r + 3)
    }
    ctx.globalAlpha = 1
  }

  const paintArea = (node: NodeObject<MapNode>, colour: string, ctx: CanvasRenderingContext2D) => {
    const n = node as MapNode
    ctx.fillStyle = colour
    ctx.beginPath()
    ctx.arc(n.x!, n.y!, radius(n) + 3, 0, Math.PI * 2)
    ctx.fill()
  }

  const linkLit = (link: MapLink) => {
    const s = idOf(link.source)
    const t = idOf(link.target)
    if (impactSet) return impactSet.has(s) && impactSet.has(t)
    if (matches) return matches.has(s) || matches.has(t)
    if (areaFocus && view === 'files') {
      const a = data.nodes.find((n) => n.id === s)?.area
      const b = data.nodes.find((n) => n.id === t)?.area
      return a === areaFocus || b === areaFocus
    }
    if (!focusId) return true
    return s === focusId || t === focusId
  }

  const linkColour = (l: LinkObject<MapNode, MapLink>) => {
    const link = l as MapLink
    const lit = linkLit(link)
    const focused = focusId && (idOf(link.source) === focusId || idOf(link.target) === focusId)
    const base = link.kind === 'tests' ? ink.paper3 : ink.line
    if (!lit) return `${base}14`
    if (focused || impactSet) {
      if (focusId && idOf(link.target) === focusId && !impactSet) return ink.amber // used by
      return base
    }
    return `${base}70`
  }

  const nodesCount = data.nodes.length

  return (
    <div className="map" ref={wrap}>
      <div className="map-controls" role="group" aria-label="Map controls">
        <input type="search" placeholder="Find a file on the map" value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label="Find a file on the map"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && matches && matches.size > 0) {
              const first = [...matches][0]
              onSelect(first)
              setQuery('')
            }
          }} />
        <div className="row">
          <span className="label">Show</span>
          <button className={`chip ${view === 'files' ? 'on' : ''}`} aria-pressed={view === 'files'}
            onClick={() => { setView('files'); setAreaFocus(null) }}>Files</button>
          <button className={`chip ${view === 'areas' ? 'on' : ''}`} aria-pressed={view === 'areas'}
            onClick={() => setView('areas')}>Areas</button>
        </div>
        <div className="row">
          <span className="label">Colour by</span>
          <button className={`chip ${colourBy === 'area' ? 'on' : ''}`} aria-pressed={colourBy === 'area'}
            onClick={() => setColourBy('area')}>Area</button>
          <button className={`chip ${colourBy === 'issues' ? 'on' : ''}`} aria-pressed={colourBy === 'issues'}
            onClick={() => setColourBy('issues')}>Issues</button>
        </div>
        <label className="check">
          <input type="checkbox" checked={showTests} onChange={(e) => setShowTests(e.target.checked)} />
          Include tests
        </label>
        {view === 'files' && (
          <>
            <label className="check">
              <input type="checkbox" checked={showOrphans} onChange={(e) => setShowOrphans(e.target.checked)} />
              Include unconnected files
            </label>
            {report.coverage.inferred_edges > 0 && (
              <label className="check">
                <input type="checkbox" checked={showInferred} onChange={(e) => setShowInferred(e.target.checked)} />
                Include best-guess links
              </label>
            )}
          </>
        )}
        {areaFocus && view === 'files' && (
          <button className="btn btn-quiet" onClick={() => setAreaFocus(null)}>
            Showing {areaLabel(areaFocus)}. Show everything
          </button>
        )}
      </div>

      <div className="map-hint" aria-live="polite">
        {view === 'files'
          ? `${plural(nodesCount, 'file')}, ${plural(data.links.length, 'connection')}. Hover to trace, click for details.`
          : 'Click an area to see its files.'}
      </div>

      {nodesCount === 0 ? (
        <div className="map-empty">
          {report.graph.nodes.length === 0
            ? 'This repository has no code files to map.'
            : 'No files to draw with these settings.'}
        </div>
      ) : (
        <ForceGraph2D<MapNode, MapLink>
          ref={graphRef}
          width={size.width}
          height={size.height}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          nodeId="id"
          nodeRelSize={1}
          nodeVal={(n) => radius(n as MapNode) ** 2}
          nodeLabel={(n) => {
            const node = n as MapNode
            return node.isArea ? `${node.label}: ${plural(node.weight, 'file')}` : node.id
          }}
          nodeCanvasObject={drawNode}
          nodePointerAreaPaint={paintArea}
          linkColor={linkColour}
          linkWidth={(l) => {
            const link = l as MapLink
            const base = view === 'areas' ? 1 + Math.sqrt(link.weight) : 1
            const focused = focusId && (idOf(link.source) === focusId || idOf(link.target) === focusId)
            return focused ? base * 1.8 : base
          }}
          linkLineDash={(l) => ((l as MapLink).tier === 'inferred' ? [3, 3] : null)}
          linkDirectionalArrowLength={view === 'areas' ? 7 : 4}
          linkDirectionalArrowRelPos={0.92}
          linkDirectionalArrowColor={linkColour}
          linkCurvature={0.06}
          onNodeHover={(n) => setHover(n ? (n as MapNode).id : null)}
          onNodeClick={(n) => {
            const node = n as MapNode
            if (node.isArea) {
              setAreaFocus(node.id)
              setView('files')
              if (node.id === 'tests') setShowTests(true)
            } else {
              onSelect(node.id)
            }
          }}
          onBackgroundClick={() => { onSelect(null); setAreaFocus(null) }}
          cooldownTicks={reduceMotion ? 0 : 180}
          warmupTicks={reduceMotion ? 200 : 30}
          onEngineStop={() => {
            if (!framed.current) {
              framed.current = true
              graphRef.current?.zoomToFit(reduceMotion ? 0 : 500, 70)
            }
          }}
          enableNodeDrag
        />
      )}

      <div className="legend" aria-label="Legend">
        {colourBy === 'area'
          ? report.architecture.areas
              .filter((a) => showTests || a.name !== 'tests')
              .slice(0, 8)
              .map((a) => (
                <span key={a.name}>
                  <i className="dot" style={{ background: colours[a.name] }} />
                  {areaLabel(a.name)}
                </span>
              ))
          : (['high', 'medium', 'low'] as const).map((s) => (
              <span key={s}>
                <i className="dot" style={{ background: s === 'high' ? ink.signal : s === 'medium' ? ink.amber : ink.low }} />
                {s[0].toUpperCase() + s.slice(1)} severity
              </span>
            ))}
        <span><i className="solid" />imports</span>
        {report.coverage.inferred_edges > 0 && <span><i className="dashed" />best guess</span>}
        {report.architecture.cycles.length > 0 && <span><i className="ring" />in an import cycle</span>}
        <span className="faint">Bigger dot: more files depend on it</span>
      </div>
    </div>
  )
}
