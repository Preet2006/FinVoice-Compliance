import { useEffect, useState } from 'react'
import { apiFetchBlob, ApiError, type RoleHeaders } from './client'

interface AudioBlobState {
  url: string | null
  isLoading: boolean
  error: string | null
}

/**
 * Fetches a call's audio as an authenticated blob (see client.ts's
 * apiFetchBlob for why: <audio> can't send our X-Role/X-User-Id headers)
 * and exposes it as an object URL for a plain <audio src=...>. Revokes
 * the previous URL whenever the call or identity changes, or on unmount.
 */
export function useCallAudioUrl(
  callId: string | undefined,
  headers: RoleHeaders | null,
  hasAudio: boolean,
): AudioBlobState {
  const [state, setState] = useState<AudioBlobState>({ url: null, isLoading: false, error: null })

  useEffect(() => {
    if (!callId || !headers || !hasAudio) {
      setState({ url: null, isLoading: false, error: null })
      return
    }

    let objectUrl: string | null = null
    let cancelled = false

    setState({ url: null, isLoading: true, error: null })

    apiFetchBlob(`/api/media/${callId}`, headers)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setState({ url: objectUrl, isLoading: false, error: null })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setState({
          url: null,
          isLoading: false,
          error: err instanceof ApiError ? err.message : 'Failed to load audio.',
        })
      })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [callId, headers?.role, headers?.userId, hasAudio])

  return state
}
