/** Thin API client: unified error envelope handling (接口设计 §1.1). */
export interface OutboundConf {
  provider: string
  context_files: string[]
  estimated_chars: number
  confirmed_at: string
}

export class ApiError extends Error {
  code: string
  status: number
  detail: any
  constructor(code: string, status: number, message: string, detail: any) {
    super(message)
    this.code = code
    this.status = status
    this.detail = detail
  }
}

async function handle(resp: Response) {
  if (resp.status === 204) return null
  const text = await resp.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!resp.ok) {
    const err = data?.error || {}
    throw new ApiError(err.code || 'HTTP_' + resp.status, resp.status, err.message || resp.statusText, err.detail)
  }
  return data
}

export async function get(url: string, params?: Record<string, any>) {
  const qs = params ? '?' + new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => [k, String(v)])).toString() : ''
  return handle(await fetch(url + qs))
}

export async function send(url: string, method: string, body?: any) {
  return handle(await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Client-Window': windowId() },
    body: body === undefined ? undefined : JSON.stringify(body)
  }))
}

export const post = (url: string, body?: any) => send(url, 'POST', body)
export const put = (url: string, body?: any) => send(url, 'PUT', body)
export const patch = (url: string, body?: any) => send(url, 'PATCH', body)
export const del = (url: string, body?: any) => send(url, 'DELETE', body)

let _window = ''
export function windowId(): string {
  if (!_window) _window = 'w-' + Math.random().toString(36).slice(2, 10)
  return _window
}

export function makeOutbound(provider: string, files: string[], chars: number): OutboundConf {
  return { provider, context_files: files, estimated_chars: chars, confirmed_at: new Date().toISOString() }
}
