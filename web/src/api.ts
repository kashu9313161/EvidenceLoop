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
      packages: number
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
  headline?: string
  kind?: string
  languages?: { name: string; files: number; loc: number }[]
  totals?: Report['summary']['totals']
  stack?: string[]
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
}

export interface FileDetail {
  path: string
  status: string
  reason: string | null
  size: number
  content: string | null
  symbols: { kind: string; name: string; start: number; end: number }[]
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
  file: (snapshot: string, path: string) =>
    request<FileDetail>(`/api/file?snapshot=${q(snapshot)}&path=${q(path)}`),
  search: (snapshot: string, text: string) =>
    request<SearchResult>(`/api/search?snapshot=${q(snapshot)}&q=${q(text)}`),
  analyzeUrl: (url: string) =>
    request<Job>('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
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
  job: (id: string) => request<Job>(`/api/jobs/${q(id)}`),
}

export const workspacePath = (snapshotId: string, tab = 'overview') =>
  `/r/${encodeURIComponent(snapshotId)}/${tab}`
