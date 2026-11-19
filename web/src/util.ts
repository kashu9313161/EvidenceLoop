import { useCallback, useEffect, useState } from 'react'
import type { Area, Severity } from './api'

// Area colours, drawn from map-pencil tones that sit on blueprint ink and on paper.
const AREA_DARK = ['#8bb8e8', '#b9a3e3', '#7fcbbb', '#e8b98c', '#d996b8', '#aacb84', '#e2d384', '#91c6e0']
const AREA_LIGHT = ['#2a68b0', '#7656b8', '#1f8a76', '#b06a1f', '#b0457a', '#5b8a2a', '#8f7a12', '#1f7aa8']
const TESTS_DARK = '#6f7f96'
const TESTS_LIGHT = '#8a97aa'

export type Theme = 'dark' | 'light'

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(currentTheme)
  const toggle = useCallback(() => {
    const next: Theme = currentTheme() === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem('el-theme', next)
    } catch {
      /* private mode: theme still applies for this visit */
    }
    setTheme(next)
    window.dispatchEvent(new Event('el-theme'))
  }, [])
  useEffect(() => {
    const sync = () => setTheme(currentTheme())
    window.addEventListener('el-theme', sync)
    return () => window.removeEventListener('el-theme', sync)
  }, [])
  return [theme, toggle]
}

export function areaPalette(areas: Area[], theme: Theme): Record<string, string> {
  const colours = theme === 'dark' ? AREA_DARK : AREA_LIGHT
  const map: Record<string, string> = {}
  let index = 0
  for (const area of areas) {
    if (area.name === 'tests') {
      map[area.name] = theme === 'dark' ? TESTS_DARK : TESTS_LIGHT
    } else {
      map[area.name] = colours[index % colours.length]
      index += 1
    }
  }
  return map
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export const plural = (n: number, word: string, many?: string) =>
  `${n.toLocaleString()} ${n === 1 ? word : (many ?? `${word}s`)}`

export const SEVERITY_RANK: Record<Severity, number> = { high: 3, medium: 2, low: 1 }

export function fileName(path: string): string {
  const parts = path.split('/')
  const last = parts[parts.length - 1]
  if ((last === '__init__.py' || last.startsWith('index.')) && parts.length > 1) {
    return `${parts[parts.length - 2]}/${last}`
  }
  return last
}

export function areaLabel(name: string): string {
  if (name === '(root)') return 'Top level'
  if (name === 'tests') return 'Tests'
  return name
}

export function languageOf(path: string): string {
  if (path.endsWith('.py') || path.endsWith('.pyi')) return 'python'
  if (path.endsWith('.ts')) return 'typescript'
  if (path.endsWith('.tsx')) return 'tsx'
  if (path.endsWith('.jsx')) return 'jsx'
  if (/\.(m|c)?js$/.test(path)) return 'javascript'
  if (path.endsWith('.json')) return 'json'
  if (path.endsWith('.toml')) return 'toml'
  if (/\.ya?ml$/.test(path)) return 'yaml'
  if (path.endsWith('.md')) return 'markdown'
  if (path.endsWith('.css') || path.endsWith('.scss')) return 'css'
  if (path.endsWith('.sql')) return 'sql'
  if (path.endsWith('.sh')) return 'bash'
  return 'text'
}

/** Render `text` with `backtick` spans as <code>. */
export function inlineCode(text: string): (string | { code: string })[] {
  const out: (string | { code: string })[] = []
  text.split(/(`[^`]+`)/g).forEach((part) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 1) {
      out.push({ code: part.slice(1, -1) })
    } else if (part) {
      out.push(part)
    }
  })
  return out
}

/** 1536 -> "1.5 KB"; 1.45e9 -> "1.4 GB". */
export function formatBytes(size: number): string {
  const units = ['bytes', 'KB', 'MB', 'GB']
  let value = size
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  if (unit === 0) return `${Math.round(value)} bytes`
  return `${value.toFixed(1).replace(/\.0$/, '')} ${units[unit]}`
}
