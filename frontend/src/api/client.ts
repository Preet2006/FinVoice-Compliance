import type { ApiErrorBody } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  code: string
  status: number

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export interface RoleHeaders {
  role: string
  userId: string
}

interface RequestOptions {
  params?: Record<string, string | number | undefined>
  headers?: RoleHeaders
  method?: string
  body?: unknown
}

function buildQuery(params?: Record<string, string | number | undefined>): string {
  if (!params) return ''
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  if (entries.length === 0) return ''
  const search = new URLSearchParams()
  for (const [key, value] of entries) {
    search.set(key, String(value))
  }
  return `?${search.toString()}`
}

async function doFetch(
  path: string,
  { params }: { params?: RequestOptions['params']; headers?: RoleHeaders },
  init: RequestInit,
): Promise<Response> {
  const url = `${API_BASE_URL}${path}${buildQuery(params)}`
  const reqHeaders = new Headers(init.headers)
  const csrf = typeof document !== 'undefined'
    ? document.cookie.split('; ').find((part) => part.startsWith('shaha_csrf='))?.split('=')[1]
    : undefined
  if (init.method && !['GET', 'HEAD', 'OPTIONS'].includes(init.method.toUpperCase()) && csrf) {
    reqHeaders.set('X-CSRF-Token', decodeURIComponent(csrf))
  }
  return fetch(url, { ...init, headers: reqHeaders, credentials: 'include' })
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { params, headers, method = 'GET', body } = options

  const response = await doFetch(
    path,
    { params, headers },
    {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
  )

  return parseJsonOrThrow<T>(response, path)
}

export async function apiUpload<T>(
  path: string,
  formData: FormData,
  headers: RoleHeaders,
): Promise<T> {
  const response = await doFetch(path, { headers }, { method: 'POST', body: formData })
  return parseJsonOrThrow<T>(response, path)
}

export async function apiFetchBlob(path: string, headers: RoleHeaders): Promise<Blob> {
  // <audio>/<img> elements can't send custom headers, so authenticated
  // media is fetched as a blob here and handed to the element as an
  // object URL — keeps the same X-Role/X-User-Id convention as every
  // other request instead of a weaker query-param auth special case.
  const response = await doFetch(path, { headers }, { method: 'GET' })
  if (!response.ok) {
    let parsed: ApiErrorBody | null = null
    try {
      parsed = await response.json()
    } catch {
      // not JSON; fall through
    }
    throw new ApiError(
      response.status,
      parsed?.error?.code ?? 'UNKNOWN_ERROR',
      parsed?.error?.message ?? `Request to ${path} failed with status ${response.status}`,
    )
  }
  return response.blob()
}

async function parseJsonOrThrow<T>(response: Response, path: string): Promise<T> {
  if (!response.ok) {
    let parsed: ApiErrorBody | null = null
    try {
      parsed = await response.json()
    } catch {
      // response body wasn't JSON; fall through to generic error below
    }
    throw new ApiError(
      response.status,
      parsed?.error?.code ?? 'UNKNOWN_ERROR',
      parsed?.error?.message ?? `Request to ${path} failed with status ${response.status}`,
    )
  }

  return response.json() as Promise<T>
}
