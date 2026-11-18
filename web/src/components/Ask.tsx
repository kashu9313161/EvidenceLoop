import { useState } from 'react'
import type { AskClaim, AskEvidence, AskResult } from '../api'
import { STATUS_LABEL, citedPaths, statusCounts, usageLine } from '../askView'
import { CloseIcon, Rich } from './bits'
import { CodeBlock } from './CodeBlock'

/** The question box in the top bar. */
export function AskBox({ busy, onAsk }: { busy: boolean; onAsk: (question: string) => void }) {
  const [text, setText] = useState('')
  return (
    <form className="ask-box" role="search" aria-label="Ask about this code"
      onSubmit={(e) => {
        e.preventDefault()
        if (text.trim() && !busy) onAsk(text.trim())
      }}>
      <input type="text" value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
        placeholder="Ask: where is…, what calls…, how does…" aria-label="Ask a question about this code" />
      <button type="submit" className="btn" disabled={busy || !text.trim()}>{busy ? 'Asking…' : 'Ask'}</button>
    </form>
  )
}

export type AskState =
  | { question: string; phase: 'asking' }
  | { question: string; phase: 'error'; error: string }
  | { question: string; phase: 'done'; result: AskResult }

interface PanelProps {
  state: AskState
  floating: boolean
  onClose: () => void
  onOpenCode: (path: string, line?: number) => void
  onShowInMap: (paths: string[]) => void
}

function Evidence({ item, onOpenCode }: { item: AskEvidence; onOpenCode: PanelProps['onOpenCode'] }) {
  const lines = item.start === item.end ? `line ${item.start}` : `lines ${item.start}–${item.end}`
  return (
    <div className="ask-evidence">
      <div className="ask-evidence-head">
        <span className="eid">{item.id}</span>
        <button className="link-btn" onClick={() => onOpenCode(item.path, item.start)} title="Open in Files">
          {item.path}
        </button>
        <span className="faint">{lines}</span>
      </div>
      <div className="ask-code">
        <CodeBlock code={item.text} path={item.path} startLine={item.start} />
      </div>
    </div>
  )
}

function ClaimCard({ claim, evidence, thresholds, onOpenCode, onShowInMap, files }: {
  claim: AskClaim
  evidence: Map<string, AskEvidence>
  thresholds: AskResult['thresholds']
  onOpenCode: PanelProps['onOpenCode']
  onShowInMap: () => void
  files: number
}) {
  const [open, setOpen] = useState(claim.status !== 'rejected')
  const j = claim.judgment
  return (
    <article className={`claim ${claim.status}`}>
      <div className="claim-head">
        <span className={`claim-status ${claim.status}`}>{STATUS_LABEL[claim.status]}</span>
        {files > 0 && claim.status !== 'rejected' && (
          <button className="link-btn" onClick={onShowInMap}>Show in map</button>
        )}
      </div>
      <p className="claim-text"><Rich text={claim.text} /></p>
      <p className="claim-reason">{claim.reason}</p>
      {j && (
        <p className="claim-scores" title={`Judged by ${j.evaluator} (${j.model_version})`}>
          support {j.support.toFixed(2)} · sufficiency {j.sufficiency.toFixed(2)} · contradiction{' '}
          {j.contradiction.toFixed(2)}
          {!thresholds.calibrated && <span className="uncalibrated">uncalibrated</span>}
        </p>
      )}
      {claim.evidence_ids.length > 0 && (
        <button className="link-btn claim-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? '▾' : '▸'} Cited: {claim.evidence_ids.join(', ')}
        </button>
      )}
      {open && claim.evidence_ids.map((id) => {
        const item = evidence.get(id)
        return item
          ? <Evidence key={id} item={item} onOpenCode={onOpenCode} />
          : <p key={id} className="claim-missing">{id} isn't in the evidence that was provided.</p>
      })}
    </article>
  )
}

export function AskPanel({ state, floating, onClose, onOpenCode, onShowInMap }: PanelProps) {
  const result = state.phase === 'done' ? state.result : null
  const evidence = new Map((result?.evidence ?? []).map((e) => [e.id, e]))
  const all = result ? citedPaths(result) : []

  return (
    <aside className={`inspector ask-panel ${floating ? 'floating' : ''}`} aria-label="Answer">
      <div className="inspector-head">
        <div className="path">You asked</div>
        <h2>{state.question}</h2>
        <button className="icon-btn close" onClick={onClose} aria-label="Close the answer">
          <CloseIcon />
        </button>
      </div>
      <div className="inspector-body">
        {state.phase === 'asking' && (
          <p className="ask-wait" role="status">Finding evidence in the code and checking each claim…</p>
        )}
        {state.phase === 'error' && <p className="explain-error" role="status">{state.error}</p>}
        {result && (
          <>
            <p className="ask-route">{result.route}</p>
            <p className={`ask-summary ${result.status !== 'answered' ? 'note' : ''}`}><Rich text={result.summary} /></p>
            {result.claims.length > 0 && (
              <div className="ask-tally">
                <span>{statusCounts(result.claims)}</span>
                {all.length > 0 && (
                  <button className="btn" onClick={() => onShowInMap(all)}>
                    Show {all.length === 1 ? 'the cited file' : `all ${all.length} cited files`} in map
                  </button>
                )}
              </div>
            )}
            {result.claims.map((claim, index) => (
              <ClaimCard key={index} claim={claim} evidence={evidence} thresholds={result.thresholds}
                onOpenCode={onOpenCode} files={citedPaths(result, claim).length}
                onShowInMap={() => onShowInMap(citedPaths(result, claim))} />
            ))}
            {result.more > 0 && <p className="faint">and {result.more} more not shown.</p>}
            <footer className="ask-foot">
              <p>{usageLine(result)}{result.model && ` Model: ${result.model}.`}</p>
              {result.kind === 'analytical' && result.claims.some((c) => c.judgment) && (
                <p>
                  Verified means support at least {result.thresholds.support.toFixed(2)}, sufficiency at
                  least {result.thresholds.sufficiency.toFixed(2)} and contradiction under{' '}
                  {result.thresholds.contradiction.toFixed(2)}. These thresholds aren't calibrated yet.
                </p>
              )}
            </footer>
          </>
        )}
      </div>
    </aside>
  )
}
