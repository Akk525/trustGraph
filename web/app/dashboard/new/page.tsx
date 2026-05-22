'use client'

import { useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { presignedUpload, uploadToS3, submitAudit, ApiError } from '@/lib/api'
import { validateAuditFileName } from '@/lib/utils'

type Step = 'idle' | 'presigning' | 'uploading' | 'submitting' | 'error'

const STEPS: { key: Step; label: string }[] = [
  { key: 'presigning', label: 'Requesting upload URL' },
  { key: 'uploading', label: 'Uploading to S3' },
  { key: 'submitting', label: 'Submitting audit job' },
]

export default function NewAuditPage() {
  const router = useRouter()
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [step, setStep] = useState<Step>('idle')
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  function pickFile(f: File | null | undefined) {
    if (!f) return
    const err = validateAuditFileName(f.name)
    if (err) {
      setError(err)
      setFile(null)
      return
    }
    setFile(f)
    setError(null)
    setStep('idle')
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    pickFile(e.target.files?.[0])
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault()
    setDragging(true)
  }

  function handleDragLeave() {
    setDragging(false)
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    pickFile(e.dataTransfer.files?.[0])
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setError(null)

    try {
      setStep('presigning')
      const { upload_url, input_s3_key } = await presignedUpload(
        file.name,
        file.type || 'application/zip',
      )

      setStep('uploading')
      await uploadToS3(upload_url, file)

      setStep('submitting')
      const job = await submitAudit(input_s3_key)

      router.push(`/dashboard/audits/${job.job_id}`)
    } catch (err) {
      setStep('error')
      setError(
        err instanceof ApiError ? err.message : 'Upload failed. Please retry.',
      )
    }
  }

  const busy =
    step === 'presigning' || step === 'uploading' || step === 'submitting'
  const currentStepIndex = STEPS.findIndex((s) => s.key === step)

  return (
    <div className="p-8 max-w-xl">
      <h1 className="text-xl font-bold text-zinc-100 mb-6">New audit</h1>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Drop zone */}
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-6">
          <p className="text-xs text-zinc-500 uppercase tracking-wider mb-3">
            Source file
          </p>
          <div
            className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors ${
              dragging
                ? 'border-cyan-500 bg-cyan-500/5'
                : 'border-zinc-700 hover:border-zinc-500'
            }`}
            onClick={() => fileRef.current?.click()}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            {file ? (
              <div>
                <p className="text-sm text-zinc-100 font-mono">{file.name}</p>
                <p className="text-xs text-zinc-500 mt-1">
                  {(file.size / 1024).toFixed(0)} KB
                </p>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    setFile(null)
                    setStep('idle')
                    setError(null)
                  }}
                  className="text-xs text-zinc-500 hover:text-zinc-300 mt-2 transition-colors"
                >
                  Remove
                </button>
              </div>
            ) : (
              <div>
                <p className="text-sm text-zinc-400">
                  {dragging ? 'Drop to select' : 'Click or drag to select file'}
                </p>
                <p className="text-xs text-zinc-600 mt-1">
                  .zip archive or .sol file
                </p>
              </div>
            )}
            <input
              ref={fileRef}
              type="file"
              accept=".zip,.sol"
              className="hidden"
              onChange={handleFileChange}
            />
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="text-red-400 text-sm bg-red-400/10 border border-red-400/20 rounded px-3 py-2">
            {error}
          </div>
        )}

        {/* Progress steps */}
        {busy && (
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            {STEPS.map((s, i) => {
              const done = i < currentStepIndex
              const active = i === currentStepIndex
              return (
                <div key={s.key} className="flex items-center gap-3 py-1.5">
                  <span
                    className={`w-4 h-4 rounded-full border flex items-center justify-center text-xs shrink-0 ${
                      done
                        ? 'border-green-500 bg-green-500/20 text-green-400'
                        : active
                          ? 'border-cyan-500 bg-cyan-500/20 text-cyan-400'
                          : 'border-zinc-700 text-zinc-600'
                    }`}
                  >
                    {done ? '✓' : i + 1}
                  </span>
                  <span
                    className={`text-xs ${
                      done
                        ? 'text-zinc-500 line-through'
                        : active
                          ? 'text-cyan-400 font-medium'
                          : 'text-zinc-600'
                    }`}
                  >
                    {s.label}
                    {active && (
                      <span className="ml-1 animate-pulse">…</span>
                    )}
                  </span>
                </div>
              )
            })}
          </div>
        )}

        <button
          type="submit"
          disabled={!file || busy}
          className="w-full bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 disabled:cursor-not-allowed text-zinc-950 font-semibold py-2 rounded transition-colors text-sm"
        >
          {busy ? 'Uploading…' : 'Submit audit'}
        </button>
      </form>

      {/* Info panel */}
      <div className="mt-6 bg-zinc-900 border border-zinc-800 rounded-lg p-5">
        <h2 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">
          How it works
        </h2>
        <ol className="text-xs text-zinc-400 space-y-1.5 list-none">
          {[
            'Your file uploads directly to S3 via presigned URL',
            'An SQS message triggers a Fargate worker',
            'The scanner runs trust-boundary analysis',
            'Findings and artifacts appear in the job detail page',
          ].map((s, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-zinc-600 font-mono shrink-0">{i + 1}.</span>
              <span>{s}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
