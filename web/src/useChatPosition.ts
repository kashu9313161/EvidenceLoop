import { useCallback, useEffect, useState } from 'react'
import { clampButton, parsePosition, type Point } from './chatPosition'

const POSITION_KEY = 'el-chat-position'
const viewport = () => ({ width: window.innerWidth, height: window.innerHeight })

/** Where the user has put the chat button (null: the default corner), kept between visits
 * and pulled back inside the window when it gets smaller. */
export function useChatPosition() {
  const [pos, setPos] = useState<Point | null>(() => {
    try {
      const saved = parsePosition(localStorage.getItem(POSITION_KEY))
      return saved && clampButton(saved, viewport())
    } catch {
      return null
    }
  })
  useEffect(() => {
    const onResize = () => setPos((p) => p && clampButton(p, viewport()))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const move = useCallback((p: Point | null, save: boolean) => {
    const next = p && clampButton(p, viewport())
    setPos(next)
    if (!save) return
    try {
      if (next) localStorage.setItem(POSITION_KEY, JSON.stringify(next))
      else localStorage.removeItem(POSITION_KEY)
    } catch {
      /* private mode: the position lasts for this visit */
    }
  }, [])
  return { pos, move }
}
