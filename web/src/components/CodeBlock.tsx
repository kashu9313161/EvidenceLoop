import { Highlight, type PrismTheme } from 'prism-react-renderer'
import { useEffect, useRef } from 'react'
import { languageOf, useTheme } from '../util'

const blueprint: PrismTheme = {
  plain: { color: '#d7e1ee', backgroundColor: 'transparent' },
  styles: [
    { types: ['comment', 'prolog', 'doctype', 'cdata'], style: { color: '#6f86a6', fontStyle: 'italic' } },
    { types: ['keyword', 'boolean', 'important'], style: { color: '#9cc3ea' } },
    { types: ['string', 'char', 'attr-value', 'template-string'], style: { color: '#e8c48c' } },
    { types: ['number', 'constant'], style: { color: '#f0a37a' } },
    { types: ['function', 'class-name', 'decorator'], style: { color: '#ffffff', fontWeight: '500' } },
    { types: ['builtin', 'tag', 'selector'], style: { color: '#9fd3c7' } },
    { types: ['operator', 'punctuation'], style: { color: '#8ea2bf' } },
  ],
}

const paper: PrismTheme = {
  plain: { color: '#1b2a40', backgroundColor: 'transparent' },
  styles: [
    { types: ['comment', 'prolog', 'doctype', 'cdata'], style: { color: '#7b8ca6', fontStyle: 'italic' } },
    { types: ['keyword', 'boolean', 'important'], style: { color: '#2a5fa8' } },
    { types: ['string', 'char', 'attr-value', 'template-string'], style: { color: '#8a5a12' } },
    { types: ['number', 'constant'], style: { color: '#b4501c' } },
    { types: ['function', 'class-name', 'decorator'], style: { color: '#0f1b2d', fontWeight: '600' } },
    { types: ['builtin', 'tag', 'selector'], style: { color: '#1f7a68' } },
    { types: ['operator', 'punctuation'], style: { color: '#5d6f8c' } },
  ],
}

interface Props {
  code: string
  path: string
  startLine?: number
  highlight?: [number, number] | null
  focus?: number | null
}

export function CodeBlock({ code, path, startLine = 1, highlight, focus }: Props) {
  const [theme] = useTheme()
  const focusRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    // Scroll only the code's own scroller, never the page around it.
    const line = focusRef.current
    const scroller = line?.closest<HTMLElement>('[data-scroller]')
    if (line && scroller) {
      // the scroller is position: relative, so offsetTop is measured from its top
      scroller.scrollTop = Math.max(0, line.offsetTop - scroller.clientHeight / 3)
    }
  }, [focus, code])

  return (
    <Highlight code={code} language={languageOf(path)} theme={theme === 'dark' ? blueprint : paper}>
      {({ tokens, getLineProps, getTokenProps }) => (
        <pre className="code" aria-label={`Source of ${path}`}>
          {tokens.map((line, index) => {
            const number = startLine + index
            const inRange = highlight && number >= highlight[0] && number <= highlight[1]
            const isFocus = focus === number
            const { className, ...lineProps } = getLineProps({ line })
            return (
              <div
                key={number}
                ref={isFocus ? focusRef : undefined}
                className={`ln ${className ?? ''} ${inRange ? 'hl' : ''} ${isFocus && !inRange ? 'focus' : ''}`}
                {...lineProps}
              >
                <span className="no">{number}</span>
                <span className="src">
                  {line.map((token, key) => (
                    <span key={key} {...getTokenProps({ token })} />
                  ))}
                  {line.length === 0 || (line.length === 1 && line[0].empty) ? '\n' : null}
                </span>
              </div>
            )
          })}
        </pre>
      )}
    </Highlight>
  )
}
