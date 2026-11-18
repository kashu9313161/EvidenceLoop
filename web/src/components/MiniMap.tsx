import { useMemo, useState } from 'react'
import type { Report } from '../api'
import { miniMap } from '../mapLayout'
import { areaLabel, areaPalette, plural, useTheme } from '../util'

interface Props {
  report: Report
  onOpen: () => void
}

/** A read-only preview of the map: no zoom, pan or drag (the page scrolls under it), a file
 * name on hover, and a click anywhere opens the full Map in the same shape. */
export function MiniMap({ report, onOpen }: Props) {
  const [theme] = useTheme()
  const [hover, setHover] = useState<string | null>(null)
  const model = useMemo(() => miniMap(report), [report])
  const colours = useMemo(() => areaPalette(report.architecture.areas, theme), [report, theme])

  if (model.nodes.length === 0) return null

  const at = new Map(model.nodes.map((n) => [n.id, n]))
  const minX = Math.min(...model.nodes.map((n) => n.x - n.r))
  const maxX = Math.max(...model.nodes.map((n) => n.x + n.r))
  const minY = Math.min(...model.nodes.map((n) => n.y - n.r))
  const maxY = Math.max(...model.nodes.map((n) => n.y + n.r))
  const span = Math.max(maxX - minX, maxY - minY, 160)
  const pad = span * 0.12
  const font = span * 0.052
  const name = (id: string) => (model.view === 'areas' ? areaLabel(id) : at.get(id)?.label ?? id)
  // The map leaves tests out, so say so: the numbers differ from the totals beside it.
  const hasTests = report.summary.totals.test_files > 0
  const caption = (model.view === 'areas'
    ? `${plural(model.files, 'file')} in ${plural(model.nodes.length, 'area')}`
    : `${plural(model.files, 'file')}, ${plural(model.connections, 'connection')}`) +
    (hasTests ? ', tests hidden' : '')
  const hovered = hover ? at.get(hover) : null
  // Biggest first; a label that would overlap one already placed is left for hover.
  const placed: { x1: number; x2: number; y1: number; y2: number }[] = []
  const labels = model.nodes.filter((n) => n.labelled).sort((a, b) => b.r - a.r).filter((n) => {
    const half = (name(n.id).length * font * 0.56) / 2
    const box = { x1: n.x - half, x2: n.x + half, y1: n.y + n.r, y2: n.y + n.r + font * 1.3 }
    if (placed.some((p) => box.x1 < p.x2 && box.x2 > p.x1 && box.y1 < p.y2 && box.y2 > p.y1)) return false
    placed.push(box)
    return true
  })
  const labelled = new Set(labels.map((n) => n.id))

  return (
    <figure className="minimap">
      <button type="button" className="minimap-canvas" onClick={onOpen}
        aria-label={`Open the map: ${caption}`}>
        <svg viewBox={`${minX - pad} ${minY - pad} ${maxX - minX + pad * 2} ${maxY - minY + pad * 2 + font}`}
          preserveAspectRatio="xMidYMid meet" aria-hidden="true">
          <g className="minimap-links" strokeWidth={span * 0.004}>
            {model.links.map((link) => {
              const a = at.get(link.source)
              const b = at.get(link.target)
              if (!a || !b) return null
              return <line key={`${link.source}>${link.target}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
            })}
          </g>
          {model.nodes.map((n) => (
            <circle key={n.id} cx={n.x} cy={n.y} r={n.r} style={{ fill: colours[n.area] }}
              className={hover && hover !== n.id ? 'dim' : undefined}
              onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} />
          ))}
          {model.nodes.filter((n) => labelled.has(n.id) || n.id === hover).map((n) => (
            <text key={`t-${n.id}`} x={n.x} y={n.y + n.r + font * 1.05} fontSize={font}
              strokeWidth={font * 0.28} className={n.id === hover ? 'on' : undefined}>
              {name(n.id)}
            </text>
          ))}
        </svg>
        {hovered && <span className="minimap-tip">{model.view === 'areas' ? areaLabel(hovered.id) : hovered.id}</span>}
      </button>
      <figcaption>
        <span>{caption}</span>
        <button type="button" className="link-btn" onClick={onOpen}>Open the map</button>
      </figcaption>
    </figure>
  )
}
