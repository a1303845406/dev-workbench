export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      signal: controller.signal,
      ...init,
    })
    const text = await response.text()
    let body: any = null
    try { body = text ? JSON.parse(text) : null } catch { body = { message: text } }
    if (!response.ok) {
      const detail = body?.error?.message ?? body?.detail?.[0]?.msg ?? body?.detail ?? body?.message ?? response.statusText
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    return body as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new Error('请求超时，请稍后重试')
    throw error
  } finally { window.clearTimeout(timeout) }
}
export const get = <T>(url: string) => request<T>(url)
export const post = <T>(url: string, body?: unknown) => request<T>(url, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
