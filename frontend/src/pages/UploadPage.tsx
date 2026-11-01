import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useUploadBatch } from '../api/uploads'
import { BatchAnalysisPanel } from '../components/analysis/BatchAnalysisPanel'
import { useRoleContext } from '../context/RoleContext'

const ACCEPTED_AUDIO_EXTENSIONS = ['.wav', '.mp3']

const panelStyle = {
  background: 'var(--surface-1)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-card)',
}

const secondaryButtonStyle = {
  minHeight: '36px',
  padding: '8px 12px',
  borderRadius: 'var(--radius-input)',
  border: '1px solid var(--border)',
  background: 'var(--surface-1)',
  color: 'var(--text-primary)',
  cursor: 'pointer',
  fontWeight: 600,
  fontSize: 'var(--text-sm)',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function dropzoneStyle(isDragging: boolean) {
  return {
    border: `1px dashed ${isDragging ? 'var(--brand)' : 'var(--border)'}`,
    borderRadius: 'var(--radius-card)',
    padding: '24px',
    textAlign: 'center' as const,
    background: isDragging ? 'var(--surface-0)' : 'var(--surface-1)',
  }
}

function UploadWorkspace() {
  const { role, activeUserId } = useRoleContext()
  const headers = activeUserId ? { role, userId: activeUserId } : null

  const [audioFiles, setAudioFiles] = useState<File[]>([])
  const [csvFile, setCsvFile] = useState<File | null>(null)
  const [draggingAudio, setDraggingAudio] = useState(false)
  const [draggingCsv, setDraggingCsv] = useState(false)
  const [rejectedAudioNames, setRejectedAudioNames] = useState<string[]>([])

  const audioInputRef = useRef<HTMLInputElement>(null)
  const csvInputRef = useRef<HTMLInputElement>(null)

  const upload = useUploadBatch(headers)

  const canStart = audioFiles.length > 0 && csvFile !== null && !upload.isPending

  function handleAudioFiles(files: FileList | null) {
    if (!files) return
    const selected = Array.from(files)
    const accepted = selected.filter((f) =>
      ACCEPTED_AUDIO_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)),
    )
    setAudioFiles((current) => [...current, ...accepted.filter((file) => !current.some((item) => item.name === file.name && item.size === file.size))])
    setRejectedAudioNames(selected.filter((file) => !accepted.includes(file)).map((file) => file.name))
  }

  function handleCsvFile(files: FileList | null) {
    if (!files || files.length === 0) return
    const file = files[0]
    if (!file.name.toLowerCase().endsWith('.csv')) return
    setCsvFile(file)
  }

  function startUpload() {
    if (!csvFile) return
    upload.mutate({ audioFiles, metadataCsv: csvFile })
  }

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <div>
        <p style={{ color: 'var(--brand)', fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Ingestion workspace</p>
        <h1 style={{ marginTop: '6px', fontSize: 'var(--text-2xl)', fontWeight: 700, letterSpacing: '-0.02em' }}>Upload Calls</h1>
        <p style={{ color: 'var(--text-secondary)', marginTop: '6px', maxWidth: '720px' }}>
          Add recordings and the matching metadata CSV to create a reviewable batch. Audio files are matched to CSV rows by the <code>filename</code> column, not by file order.
        </p>
      </div>

      <div style={{ ...panelStyle, display: 'flex', alignItems: 'center', gap: '14px', padding: '14px 16px', flexWrap: 'wrap' }}>
        <span style={{ width: '28px', height: '28px', display: 'grid', placeItems: 'center', borderRadius: '50%', background: 'var(--brand)', color: '#fff', fontWeight: 700 }}>1</span>
        <div style={{ flex: '1 1 220px' }}><strong>Select the batch files</strong><p style={{ marginTop: '2px', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>Choose one or more recordings and one metadata file.</p></div>
        <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>Then validate and start processing below</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '16px' }}>
        <div
          style={{ ...panelStyle, ...dropzoneStyle(draggingAudio), textAlign: 'left' }}
          onDragOver={(e) => {
            e.preventDefault()
            setDraggingAudio(true)
          }}
          onDragLeave={() => setDraggingAudio(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDraggingAudio(false)
            handleAudioFiles(e.dataTransfer.files)
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
            <div><p style={{ color: 'var(--brand)', fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Step 1</p><p style={{ marginTop: '6px', fontWeight: 700, fontSize: 'var(--text-lg)' }}>Audio recordings</p></div>
            <span style={{ padding: '4px 8px', borderRadius: '999px', background: 'var(--surface-2)', color: 'var(--text-secondary)', fontSize: 'var(--text-xs)', fontWeight: 600 }}>WAV / MP3</span>
          </div>
          <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', margin: '8px 0' }}>
            Drop multiple call recordings here, or browse from your computer.
          </p>
          <label style={{ ...secondaryButtonStyle, display: 'inline-flex', alignItems: 'center' }}>
            Browse files
            <input
              ref={audioInputRef}
              type="file"
              accept=".wav,.mp3,audio/wav,audio/mpeg"
              multiple
              onChange={(e) => handleAudioFiles(e.target.files)}
              style={{ display: 'none' }}
            />
          </label>
          {audioFiles.length > 0 && (
            <div style={{ marginTop: '16px', display: 'grid', gap: '6px' }}>
              <p style={{ color: 'var(--success)', fontSize: 'var(--text-sm)', fontWeight: 600 }}>{audioFiles.length} recording{audioFiles.length === 1 ? '' : 's'} selected</p>
              {audioFiles.map((file) => <div key={`${file.name}-${file.size}`} style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', padding: '7px 9px', borderRadius: 'var(--radius-input)', background: 'var(--surface-0)', fontSize: 'var(--text-sm)' }}><span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</span><span className="font-mono" style={{ color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{formatBytes(file.size)}</span><button type="button" aria-label={`Remove ${file.name}`} onClick={() => setAudioFiles((current) => current.filter((item) => item !== file))} style={{ border: 0, background: 'transparent', color: 'var(--critical)', cursor: 'pointer', fontWeight: 700 }}>×</button></div>)}
            </div>
          )}
          {rejectedAudioNames.length > 0 && <p role="alert" style={{ marginTop: '12px', color: 'var(--critical)', fontSize: 'var(--text-sm)' }}>Skipped unsupported file{rejectedAudioNames.length === 1 ? '' : 's'}: {rejectedAudioNames.join(', ')}</p>}
        </div>

        <div
          style={{ ...panelStyle, ...dropzoneStyle(draggingCsv), textAlign: 'left' }}
          onDragOver={(e) => {
            e.preventDefault()
            setDraggingCsv(true)
          }}
          onDragLeave={() => setDraggingCsv(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDraggingCsv(false)
            handleCsvFile(e.dataTransfer.files)
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
            <div><p style={{ color: 'var(--brand)', fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Step 2</p><p style={{ marginTop: '6px', fontWeight: 700, fontSize: 'var(--text-lg)' }}>Metadata CSV</p></div>
            <span style={{ padding: '4px 8px', borderRadius: '999px', background: 'var(--surface-2)', color: 'var(--text-secondary)', fontSize: 'var(--text-xs)', fontWeight: 600 }}>Required</span>
          </div>
          <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', margin: '8px 0' }}>
            Required columns: call_id, filename, caller_id, office_id, account_type, start_time, language
          </p>
          <label style={{ ...secondaryButtonStyle, display: 'inline-flex', alignItems: 'center' }}>
            Browse files
            <input
              ref={csvInputRef}
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => handleCsvFile(e.target.files)}
              style={{ display: 'none' }}
            />
          </label>
          {csvFile && (
            <div style={{ marginTop: '16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', padding: '9px', borderRadius: 'var(--radius-input)', background: 'var(--surface-0)', fontSize: 'var(--text-sm)' }}><span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{csvFile.name}</span><span className="font-mono" style={{ color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{formatBytes(csvFile.size)}</span><button type="button" aria-label="Remove metadata CSV" onClick={() => setCsvFile(null)} style={{ border: 0, background: 'transparent', color: 'var(--critical)', cursor: 'pointer', fontWeight: 700 }}>×</button></div>
          )}
        </div>
      </div>

      <div style={{ ...panelStyle, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '16px', padding: '16px', flexWrap: 'wrap' }}>
        <div><p style={{ fontWeight: 700 }}>Ready to validate this batch?</p><p style={{ marginTop: '3px', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>{audioFiles.length} audio file{audioFiles.length === 1 ? '' : 's'} · {csvFile ? 'metadata CSV selected' : 'metadata CSV still needed'}</p></div>
        <button onClick={startUpload} disabled={!canStart} style={{ minHeight: '40px', padding: '10px 18px', borderRadius: 'var(--radius-input)', border: 'none', background: canStart ? 'var(--brand)' : 'var(--surface-2)', color: canStart ? '#fff' : 'var(--text-secondary)', cursor: canStart ? 'pointer' : 'not-allowed', fontWeight: 700 }}>
          {upload.isPending ? 'Uploading…' : 'Start Processing Batch'}
        </button>
      </div>

      {upload.isError && (
        <p
          role="alert"
          style={{
            padding: '14px 16px',
            background: 'var(--error-bg)',
            borderRadius: 'var(--radius-input)',
            color: 'var(--critical)',
          }}
        >
          {upload.error instanceof ApiError
            ? upload.error.message
            : 'Upload failed unexpectedly.'}
        </p>
      )}

      {upload.data && (
        <div style={{ ...panelStyle, padding: '18px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '16px', flexWrap: 'wrap' }}>
            <div><p style={{ color: 'var(--brand)', fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Batch created</p><p className="font-mono" style={{ marginTop: '5px', fontWeight: 700 }}>{upload.data.batch_id}</p></div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}><span style={{ padding: '5px 9px', borderRadius: '999px', background: 'var(--success-bg, #E6F4EA)', color: 'var(--success)', fontSize: 'var(--text-xs)', fontWeight: 700 }}>{upload.data.accepted.length} accepted</span><span style={{ padding: '5px 9px', borderRadius: '999px', background: upload.data.rejected.length ? 'var(--error-bg)' : 'var(--surface-2)', color: upload.data.rejected.length ? 'var(--critical)' : 'var(--text-secondary)', fontSize: 'var(--text-xs)', fontWeight: 700 }}>{upload.data.rejected.length} rejected</span></div>
          </div>
          {upload.data.accepted.length > 0 && (
            <>
              <p
                style={{
                  fontSize: 'var(--text-sm)',
                  color: 'var(--text-secondary)',
                }}
              >
                Accepted calls are created as "queued". Nothing is analysed until you start it below.
              </p>
              <BatchAnalysisPanel
                key={upload.data.batch_id}
                accepted={upload.data.accepted}
                headers={headers}
                canRun={role === 'manager' || role === 'head'}
              />
            </>
          )}

          {upload.data.rejected.length > 0 && (
            <div
              style={{
                marginTop: '18px',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-card)',
                overflow: 'hidden',
                background: 'var(--surface-0)',
              }}
            >
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  {upload.data.rejected.map((r, i) => (
                    <tr key={`rejected-${r.identifier}-${i}`}>
                      <td style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)' }} className="font-mono">
                        {r.identifier}
                      </td>
                      <td style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', color: 'var(--critical)' }}>
                        ✗ Rejected — {r.reason}
                        {r.detail ? `: ${r.detail}` : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Only a manager or the head of collections may upload (the server enforces this too and
// answers a caller with 403). A caller who opens /upload directly sees why, not a form.
export function UploadPage() {
  const { role } = useRoleContext()
  if (role === 'caller') {
    return (
      <div role="alert" data-testid="upload-denied" style={{ maxWidth: '560px' }}>
        <h1 style={{ fontSize: 'var(--text-2xl)', fontWeight: 600 }}>Upload not available</h1>
        <p style={{ marginTop: '8px', color: 'var(--text-secondary)' }}>
          Only a manager or the head of collections can upload call recordings. You can still open your own calls from the Calls page.
        </p>
        <Link to="/calls" style={{ display: 'inline-block', marginTop: '12px', color: 'var(--brand)' }}>
          Go to Calls
        </Link>
      </div>
    )
  }
  return <UploadWorkspace />
}
