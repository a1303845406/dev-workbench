/** Global SSE subscription with reconnect (接口设计 §4). */
type Handler = (event: string, data: any) => void
const handlers = new Set<Handler>()
let source: EventSource | null = null
let connected = false
const statusWatchers = new Set<(ok: boolean) => void>()

export function onSse(h: Handler) {
  handlers.add(h)
  return () => handlers.delete(h)
}

export function onStatus(w: (ok: boolean) => void) {
  statusWatchers.add(w)
  w(connected)
  return () => statusWatchers.delete(w)
}

export function connectSse() {
  if (source) return
  source = new EventSource('/api/events')
  source.onopen = () => { connected = true; statusWatchers.forEach(w => w(true)) }
  source.onerror = () => { connected = false; statusWatchers.forEach(w => w(false)) }
  const relay = (name: string) => {
    source!.addEventListener(name, (e: MessageEvent) => {
      let data: any = null
      try { data = JSON.parse(e.data) } catch { data = e.data }
      handlers.forEach(h => h(name, data))
    })
  }
  ;['scan.progress', 'scan.completed', 'pipeline.state_changed', 'pipeline.round_delta',
    'pipeline.round_done', 'ring.started', 'ring.delta', 'ring.completed', 'ring.failed',
    'ring.suspended', 'plan.status', 'handoff.stale', 'config.reloaded',
    'project.lock_warning', 'backup.completed'].forEach(relay)
}
