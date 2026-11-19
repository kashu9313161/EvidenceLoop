// Types mirror analysis/understanding/report.py. Keep the two in step.

export type Severity = 'high' | 'medium' | 'low'

export interface Snippet {
  start: number
  lines: string[]
  truncated?: boolean
}

export interface Evidence {
  path: string
  start: number
  end: number
  label: string
  snippet: Snippet
}

export interface Issue {
  id: string
  rule: string
  category: string
  severity: Severity
  title: string
  summary: string
  why: string
  suggestion: string
  confidence: 'confirmed' | 'heuristic'
  score: number
  files: string[]
  evidence: Evidence[]
}

export interface FunctionMetric {
  name: string
  start: number
  end: number
  complexity: number
  lines: number
  nesting: number
  /** Symbol id (`path::qualified_name`), for its explanation card. */
  id: string | null
  public: boolean
  /** Distinct functions (or module top levels) that call it / that it calls. */
  callers: number
  callees: number
}

export interface FileInfo {
  path: string
  name: string
  language: string
  status: string
  failure: string | null
  area: string
  role: string
  role_label: string
  role_reason: string
  description: string
  docstring: string | null
  loc: number
  functions: number
  classes: number
  max_complexity: number
  fan_in: number
  fan_out: number
  impact: number
  importance: number
  is_test: boolean
  is_entry: boolean
  in_cycle: boolean
  tested_by: string[]
  used_by: string[]
  uses: string[]
  packages: string[]
  insights: string[]
  severity: Severity | null
  function_metrics: FunctionMetric[]
}

export interface GraphNode {
  id: string
  name: string
  area: string
  role: string
  loc: number
  fan_in: number
  fan_out: number
  importance: number
  in_cycle: boolean
  status: string
  is_test: boolean
  is_entry: boolean
  severity: Severity | null
  insights: number
}

export interface GraphEdge {
  source: string
  target: string
  tier: 'confirmed' | 'inferred'
  kind: 'imports' | 'tests'
  /** Only imported for type hints: never runs, so not a dependency. */
  type_only: boolean
  lines: number[]
}

export interface Area {
  name: string
  files: string[]
  /** An examples/docs folder rather than the project's own code. */
  supporting: boolean
  loc: number
  roles: Record<string, number>
  key_file: string
  description: string
  depends_on: { area: string; edges: number }[]
  insights: number
}

export interface FlowStep {
  path: string
  depth: number
  parent: string | null
  line: number | null
}

export interface Flow {
  entry: string
  kind: string
  reason: string
  steps: FlowStep[]
  reach: number
}

export interface Route {
  method: string
  path: string
  handler: string
  file: string
  line: number
  supporting: boolean
}

export interface StackItem {
  name: string
  category: string
  files: number
  evidence: string
}

export interface Report {
  version: string
  snapshot: {
    id: string
    commit: string | null
    created_at: string
    repo: { name: string; owner: string | null; url: string | null; source: string | null }
  }
  summary: {
    headline: string
    facts: string[]
    kind: string
    readme: string | null
    readme_path: string | null
    languages: { name: string; files: number; loc: number }[]
    stack: StackItem[]
    totals: {
      files: number
      source_files: number
      supporting_files: number
      test_files: number
      loc: number
      source_loc: number
      functions: number
      classes: number
      connections: number
      /** Third-party packages the project's own code imports at runtime. */
      packages: number
      /** Used only by tests, docs, examples or type hints. */
      dev_packages: number
      insights: number
      by_severity: Partial<Record<Severity, number>>
    }
    key_files: { path: string; role: string; fan_in: number; impact: number; why: string }[]
  }
  coverage: {
    discovered: number
    analyzed: number
    indexed: number
    skipped: number
    failed: number
    skipped_by_reason: { reason: string; files: number }[]
    skipped_files: { path: string; reason: string }[]
    failed_files: { path: string; reason: string }[]
    /** How much of the source was analysed (a folder, or the most important files of a big
     * repository); absent in reports older than 2.6. */
    scope?: CoverageScope
    /** Text that isn't analysed code (READMEs, docs, config); absent in older reports. */
    indexed_files?: string[]
    imports: {
      internal_resolved: number
      internal_unresolved: number
      external: number
      resolution_rate: number
      unresolved: { path: string; module: string; line: number }[]
    }
    confirmed_edges: number
    inferred_edges: number
  }
  architecture: { areas: Area[]; flows: Flow[]; routes: Route[]; cycles: string[][] }
  files: FileInfo[]
  graph: {
    nodes: GraphNode[]
    edges: GraphEdge[]
    area_edges: { source: string; target: string; edges: number }[]
  }
  insights: Issue[]
  /** Function-level calls (Python): resolved through imports, or guessed from a unique name. */
  calls: { resolved: number; confirmed: number; inferred: number; unresolved: number; dynamic: number }
  dependencies: {
    external: { package: string; kind: string; files: number; display: string }[]
    manifests: {
      path: string
      kind: string
      requirements: { name: string; spec: string; line: number; pinned: boolean }[]
    }[]
  }
  seconds: number
}

export interface RepoSummary {
  snapshot_id: string
  name: string
  owner: string | null
  source: string
  url: string | null
  commit: string | null
  created_at: string
  files: number
  analyzed: boolean
  /** A folder of a big repository, '*' for its most important files, or null for all of it. */
  scope?: string | null
  headline?: string
  kind?: string
  languages?: { name: string; files: number; loc: number }[]
  totals?: Report['summary']['totals']
  stack?: string[]
}

export interface CoverageScope {
  kind: 'folder' | 'priority' | null
  folder: string | null
  source_files: number
  analysed_source_files: number
  left_out_folders: { folder: string; files: number }[]
  sentence: string
}

/** What the preflight found before anything was downloaded. */
export interface IngestPlan {
  status: 'ok' | 'needs_scope'
  scope: string | null
  summary: string
  repo_bytes: number
  total_files: number
  source_files: number
  source_bytes: number
  selected_files: number
  selected_bytes: number
  skipped_by_reason: { reason: string; files: number; bytes: number }[]
  /** One level below the scope; `top_level_files` marks the files directly in it. */
  folders: { folder: string; files: number; bytes: number; top_level_files: boolean }[]
  limits: { files: number; bytes: number }
}

export interface SourceRules {
  supported_extensions: string[]
  ignored_directories: Record<string, string[]>
  vendor_directory: string
  vendor_extensions: string[]
  skipped_extensions: Record<string, string[]>
  ignored_file_names: string[]
  svg_skip_bytes: number
  max_file_bytes: number
}

export interface IngestConfig {
  rules: SourceRules
  limits: { source_files: number; source_bytes: number; upload_bytes: number }
}

export interface Job {
  id: string
  source: string
  stage: string
  label: string
  done: number
  total: number
  message: string
  snapshot_id: string | null
  error: string | null
  plan?: IngestPlan | null
}

export interface FileDetail {
  path: string
  status: string
  reason: string | null
  size: number
  content: string | null
  symbols: { kind: string; name: string; start: number; end: number }[]
}

/** Where an answer came from: worked out from the code, the code's docstring, written by AI,
 * or written by AI and checked against the code. */
export type RowSource = 'code' | 'docstring' | 'ai' | 'ai-checked'

export interface Row {
  text: string
  source: RowSource
}

export interface KeyPart {
  symbol: string
  kind: string
  start: number
  end: number
  does: string
  source: RowSource
}

/** An AI explanation of a file: the five questions. */
export interface Explanation {
  /** Same as purpose (kept for one-line glances). */
  summary: string
  purpose: string
  inputs: string
  steps: KeyPart[]
  outputs: string
  why: string
  dropped: number
  model: string
  sent: string
  created_at: string
}

/** The five questions about a file answered from the code alone. */
export interface FileFacts {
  purpose: Row | null
  inputs: Row
  steps: KeyPart[]
  outputs: Row & { names: { name: string; by: string[] }[] }
  why: Row
}

export interface ExplainResponse {
  available: boolean
  reason?: string
  status?: 'ready' | 'missing' | 'unavailable'
  explanation?: Explanation | null
  facts?: FileFacts | null
}

export interface CardInput {
  name: string
  kind: 'positional_only' | 'positional' | 'keyword_only' | 'var_positional' | 'var_keyword'
  type: string | null
  default: string | null
  meaning: string | null
  source: RowSource
}

export interface CardStep {
  text: string
  start: number
  end: number
  source: RowSource
}

export interface CardCall {
  id: string
  name: string
  path?: string
  line: number
  tier: 'confirmed' | 'inferred'
}

export type SideEffectKind = 'file' | 'network' | 'database' | 'env' | 'subprocess' | 'log'

/** A function's card: the five questions plus side effects and an example. */
export interface FunctionCard {
  symbol: string
  name: string
  kind: 'function' | 'method'
  path: string
  start: number
  end: number
  complexity: number
  trivial: boolean
  purpose: Row | null
  inputs: CardInput[]
  steps: CardStep[]
  output: { type: string | null; lines: number[]; text: string; meaning: string | null; source: RowSource }
  why: Row & { callers: (CardCall & { path: string })[] }
  callees: CardCall[]
  raises: { name: string; line: number }[]
  side_effects: { kind: SideEffectKind; call: string; line: number }[]
  example: {
    path: string
    start: number
    end: number
    code: string
    from_test: boolean
    caller: string
    source: RowSource
  } | null
  ai: {
    /** off: no API key; trivial: short enough to read; missing: not written yet. */
    status: 'off' | 'trivial' | 'ready' | 'missing'
    reason: string | null
    model?: string
    sent?: string
    dropped?: number
    created_at?: string
  }
}

export interface FunctionCardResponse {
  available: boolean
  status: 'off' | 'trivial' | 'ready' | 'missing' | 'unavailable'
  reason?: string
  card?: FunctionCard
}

export type ClaimStatus = 'verified' | 'insufficient' | 'rejected'

export interface AskEvidence {
  id: string
  path: string
  start: number
  end: number
  kind: 'code' | 'import' | 'issue'
  label: string
  text: string
  sha256: string
}

export interface AskJudgment {
  support: number
  contradiction: number
  sufficiency: number
  evaluator: string
  model_version: string
  reason: string
}

export interface AskClaim {
  text: string
  evidence_ids: string[]
  status: ClaimStatus
  reason: string
  checks: { check: string; ok: boolean; detail: string }[]
  judgment: AskJudgment | null
}

export interface AskUsage {
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  max_tokens: number
  max_cost_usd: number
}

export interface AskResult {
  id: string
  snapshot_id: string
  question: string
  kind: 'lookup' | 'analytical'
  lookup: 'where' | 'callers' | 'users' | null
  route: string
  status: 'answered' | 'needs_ai' | 'budget' | 'failed'
  summary: string
  more: number
  claims: AskClaim[]
  evidence: AskEvidence[]
  usage: AskUsage
  provider: string | null
  model: string | null
  thresholds: { support: number; sufficiency: number; contradiction: number; calibrated: boolean }
  created_at: string
}

export type AiStatus =
  | { available: false; reason: string }
  | {
      available: true
      provider: string
      model: string
      judge: { kind: 'llm' | 'jev'; provider: string | null; model: string | null; available?: boolean; reason?: string }
    }

export interface SearchResult {
  files: string[]
  symbols: { name: string; path: string; start: number; end: number }[]
  content: { relative_path: string; snippet: string }[]
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : detail
    } catch {
      /* keep the status text */
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

const q = encodeURIComponent

export const api = {
  repos: () => request<RepoSummary[]>('/api/repos'),
  report: (snapshot: string) => request<Report>(`/api/report?snapshot=${q(snapshot)}`),
  /** The report, or the job rebuilding it when it was saved by an older version. */
  reportOrRebuild: (snapshot: string) =>
    request<Report | { rebuilding: true; job: Job }>(`/api/report?snapshot=${q(snapshot)}&background=1`),
  file: (snapshot: string, path: string) =>
    request<FileDetail>(`/api/file?snapshot=${q(snapshot)}&path=${q(path)}`),
  search: (snapshot: string, text: string) =>
    request<SearchResult>(`/api/search?snapshot=${q(snapshot)}&q=${q(text)}`),
  /** `scope`: a folder of a big repository, or '*' for its most important files. */
  analyzeUrl: (url: string, scope?: string) =>
    request<Job>('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(scope ? { url, scope } : { url }),
    }),
  reanalyze: (snapshot_id: string) =>
    request<Job>('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ snapshot_id }),
    }),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<Job>('/api/analyze/upload', { method: 'POST', body })
  },
  /** A folder chosen in the browser: only the files that will be analysed, with their paths. */
  uploadFolder: (files: { path: string; file: File }[], name: string) => {
    const body = new FormData()
    body.append('name', name)
    for (const { path, file } of files) {
      body.append('path', path)
      body.append('file', file, 'blob')
    }
    return request<Job>('/api/analyze/folder', { method: 'POST', body })
  },
  config: () => request<IngestConfig>('/api/config'),
  job: (id: string) => request<Job>(`/api/jobs/${q(id)}`),
  /** Ask a question: lookups use no AI; open questions are answered with checked claims. */
  ask: (snapshot: string, question: string) =>
    request<AskResult>('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ snapshot, question }),
    }),
  /** A lookup answered from the index as you type (no AI, nothing stored). */
  lookup: (snapshot: string, text: string) =>
    request<AskResult | { kind: 'analytical' }>(`/api/lookup?snapshot=${q(snapshot)}&q=${q(text)}`),
  /** Delete a repository's analysis from this computer (GitHub is untouched). */
  removeRepo: (snapshot: string) =>
    request<{ removed: string }>('/api/repos/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ snapshot }),
    }),
  /** Recent questions and answers for this repository, newest first. */
  asks: (snapshot: string) => request<AskResult[]>(`/api/asks?snapshot=${q(snapshot)}`),
  /** Which models answer and judge. */
  ai: () => request<AiStatus>('/api/ai'),
  /** The cached AI explanation of a file; never calls the model. */
  explanation: (snapshot: string, path: string) =>
    request<ExplainResponse>(`/api/explain?snapshot=${q(snapshot)}&path=${q(path)}`),
  /** Write the explanation now (or rewrite it with refresh). */
  explain: (snapshot: string, path: string, refresh = false) =>
    request<ExplainResponse>('/api/explain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ snapshot, path, refresh }),
    }),
  /** A function's card from the code, plus any cached AI answer; never calls the model. */
  functionCard: (snapshot: string, symbol: string) =>
    request<FunctionCardResponse>(`/api/explain/function?snapshot=${q(snapshot)}&symbol=${q(symbol)}`),
  /** Write the function's AI answer now (or rewrite it with refresh). */
  explainFunction: (snapshot: string, symbol: string, refresh = false) =>
    request<FunctionCardResponse>('/api/explain/function', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ snapshot, symbol, refresh }),
    }),
}

export const workspacePath = (snapshotId: string, tab = 'overview') =>
  `/r/${encodeURIComponent(snapshotId)}/${tab}`
