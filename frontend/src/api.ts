export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) }, ...init })
  const text = await response.text()
  const body = text ? JSON.parse(text) : null
  if (!response.ok) throw new Error(body?.error?.message ?? response.statusText)
  return body as T
}
export const get = <T>(url: string) => request<T>(url)
export const post = <T>(url: string, body?: unknown) => request<T>(url, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
