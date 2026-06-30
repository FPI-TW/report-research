import type { AskEvent } from './sseEvents'

/** SSE frame（event:/data: 行）→ 型別化事件；無 data 或壞 JSON 回 null。 */
export function parseFrame(frame: string): AskEvent | null {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return { event, data: JSON.parse(data) } as AskEvent
  } catch {
    return null
  }
}
