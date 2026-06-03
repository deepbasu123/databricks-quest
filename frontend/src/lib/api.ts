import { useCallback, useEffect, useRef, useState } from 'react'

export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal })
  if (!res.ok) throw new Error(`Request failed: ${res.status}`)
  return (await res.json()) as T
}

export type ApiState<T> = {
  data: T | null
  loading: boolean
  error: string | null
  /** True once a real network response (success or failure) has resolved. */
  loaded: boolean
  reload: () => void
}

/**
 * Fetch a JSON resource with loading/error state. `fallback` is shown only as a
 * local-preview convenience and never overrides a successful API response.
 */
export function useApi<T>(url: string, fallback: T | null = null): ApiState<T> {
  const [data, setData] = useState<T | null>(fallback)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [nonce, setNonce] = useState(0)
  const fallbackRef = useRef(fallback)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    setError(null)
    fetchJson<T>(url, ctrl.signal)
      .then((json) => {
        setData(json)
        setError(null)
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return
        // Keep fallback (preview) data usable while surfacing the error.
        setData((prev) => prev ?? fallbackRef.current)
        setError(err instanceof Error ? err.message : 'Unknown error')
      })
      .finally(() => {
        if (ctrl.signal.aborted) return
        setLoading(false)
        setLoaded(true)
      })
    return () => ctrl.abort()
  }, [url, nonce])

  return { data, loading, error, loaded, reload }
}
