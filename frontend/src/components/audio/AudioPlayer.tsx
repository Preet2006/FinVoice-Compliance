import { forwardRef, useImperativeHandle, useRef } from 'react'

export interface AudioPlayerHandle {
  seekTo: (seconds: number) => void
}

interface AudioPlayerProps {
  src: string | null
  isLoading: boolean
  error: string | null
  onTimeUpdate?: (currentTime: number) => void
}

export const AudioPlayer = forwardRef<AudioPlayerHandle, AudioPlayerProps>(
  ({ src, isLoading, error, onTimeUpdate }, ref) => {
    const audioRef = useRef<HTMLAudioElement>(null)

    useImperativeHandle(ref, () => ({
      seekTo: (seconds: number) => {
        const el = audioRef.current
        if (!el) return
        el.currentTime = seconds
        void el.play().catch(() => {
          // Autoplay can be blocked by the browser; seeking still worked,
          // which is the part this component's contract promises.
        })
      },
    }))

    if (isLoading) {
      return (
        <div style={{ padding: '16px', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>
          Loading audio…
        </div>
      )
    }

    if (error) {
      return (
        <div
          role="alert"
          style={{
            padding: '12px',
            background: 'var(--error-bg)',
            color: 'var(--critical)',
            borderRadius: 'var(--radius-input)',
            fontSize: 'var(--text-sm)',
          }}
        >
          Audio unavailable: {error}
        </div>
      )
    }

    if (!src) {
      return (
        <div style={{ padding: '16px', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>
          No recording available for this call.
        </div>
      )
    }

    return (
      <audio
        ref={audioRef}
        src={src}
        controls
        style={{ width: '100%' }}
        onTimeUpdate={(e) => onTimeUpdate?.(e.currentTarget.currentTime)}
      />
    )
  },
)

AudioPlayer.displayName = 'AudioPlayer'
