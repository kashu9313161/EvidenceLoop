import type { Severity } from '../api'
import { inlineCode, useTheme } from '../util'

const SEVERITY_LABEL: Record<Severity, string> = { high: 'High', medium: 'Medium', low: 'Low' }

export function Sev({ level }: { level: Severity }) {
  return <span className={`sev ${level}`}>{SEVERITY_LABEL[level]}</span>
}

export function Rich({ text }: { text: string }) {
  return (
    <>
      {inlineCode(text).map((part, index) =>
        typeof part === 'string' ? part : <code key={index}>{part.code}</code>,
      )}
    </>
  )
}

export function ThemeToggle() {
  const [theme, toggle] = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button className="icon-btn" onClick={toggle} aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}>
      {theme === 'dark' ? (
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" />
        </svg>
      ) : (
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
        </svg>
      )}
    </button>
  )
}

export function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
      <g stroke="var(--line)" strokeWidth="1.8" fill="none">
        <path d="M9 21 L16 10 L23 19" />
        <path d="M16 10 L16 24" />
      </g>
      <circle cx="9" cy="21" r="3" fill="var(--paper)" />
      <circle cx="16" cy="10" r="3.4" fill="var(--paper)" />
      <circle cx="23" cy="19" r="3" fill="var(--signal)" />
      <circle cx="16" cy="24" r="2.2" fill="var(--line)" />
    </svg>
  )
}

export function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}
