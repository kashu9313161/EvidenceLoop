import { useState } from 'react'
import type { AskClaim, AskEvidence, AskResult } from '../api'
import { STATUS_LABEL, citedPaths, statusCounts, usageLine } from '../askView'
import { Rich } from './bits'
import { CodeBlock } from './CodeBlock'

export interface AnswerActions {
  onOpenCode: (path: string, line?: number) => void
  onShowInMap: (paths: string[]) => void
}

function Evidence({ item, onOpenCode }: { item: AskEvidence; onOpenCode: AnswerActions['onOpenCode'] }) {
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

function ClaimCard({ claim, evidence, thresholds, files, onOpenCode, onShowInMap }: {
  claim: AskClaim
  evidence: Map<string, AskEvidence>
  thresholds: AskResult['thresholds']
  files: number
  onOpenCode: AnswerActions['onOpenCode']
  onShowInMap: () => void
}) {
  // Lookups list many hits: keep their code folded until asked for.
  const [open, setOpen] = useState(claim.status !== 'rejected' && claim.judgment !== null)
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

/** An answer: summary, one card per claim with its status, reason, scores and cited code,
 * and what it cost. Used for lookups and AI answers alike. */
export function AnswerView({ result, onOpenCode, onShowInMap }: { result: AskResult } & AnswerActions) {
  const evidence = new Map(result.evidence.map((e) => [e.id, e]))
  const all = citedPaths(result)
  return (
    <div className="answer">
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
    </div>
  )
}
