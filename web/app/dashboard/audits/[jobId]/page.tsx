'use client'

import { useState, useEffect } from 'react'
import { use } from 'react'
import Link from 'next/link'
import { getJob, listArtifacts, Job, Artifact, ApiError } from '@/lib/api'
import { StatusBadge } from '@/components/StatusBadge'
import { CopyButton } from '@/components/CopyButton'

const ACTIVE = new Set<string>(['queued', 'running'])
const POLL_MS = 5000

const SEVERITY_CLASSES: Record<string, string> = {
  critical: 'text-red-400 bg-red-400/10 border-red-400/20',
  high: 'text-orange-400 bg-orange-400/10 border-orange-400/20',
  medium: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20',
  low: 'text-blue-400 bg-blue-400/10 border-blue-400/20',
}

function SeverityBadge({ level, count }: { level: string; count: number }) {
  const cls =
    SEVERITY_CLASSES[level] ?? 'text-zinc-400 bg-zinc-400/10 border-zinc-400/20'
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono border ${cls}`}
    >
      <span className="font-bold text-sm">{count}</span>
      <span>{level}</span>
    </span>
  )
}

function DetailRow({ label, value, className }: { label: string; value: React.ReactNode; className?: string }) {
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className={`font-mono text-xs text-zinc-100 ${className ?? ''}`}>{value}</dd>
    </>
  )
}

export default function JobDetailPage({
  params,
}: {
  params: Promise<{ jobId: string }>
}) {
  const { jobId } = use(params)

  const [job, setJob] = useState<Job | null>(null)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      try {
        const j = await getJob(jobId)
        setJob(j)
        if (j.status === 'succeeded') {
          const arts = await listArtifacts(jobId)
          setArtifacts(arts.artifacts)
        }
      } catch (err) {
        if (err instanceof ApiError && err.status !== 401) {
          setError(err.status === 404 ? 'Job not found.' : err.message)
        }
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [jobId])

  // Poll until terminal
  useEffect(() => {
    if (!job || !ACTIVE.has(job.status)) return

    const id = setInterval(async () => {
      try {
        const fresh = await getJob(jobId)
        setJob(fresh)
        if (fresh.status === 'succeeded') {
          const arts = await listArtifacts(jobId)
          setArtifacts(arts.artifacts)
        }
      } catch {
        // Ignore transient poll errors
      }
    }, POLL_MS)

    return () => clearInterval(id)
  }, [job, jobId])

  if (loading) {
    return (
      <div className="p-8 max-w-3xl space-y-4">
        <div className="h-4 w-24 bg-zinc-800 rounded animate-pulse" />
        <div className="h-8 w-80 bg-zinc-800 rounded animate-pulse" />
        <div className="h-32 bg-zinc-900 border border-zinc-800 rounded-lg animate-pulse" />
        <div className="h-24 bg-zinc-900 border border-zinc-800 rounded-lg animate-pulse" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-8">
        <Link
          href="/dashboard"
          className="text-xs text-zinc-500 hover:text-zinc-300 mb-4 inline-block"
        >
          ← Back to audits
        </Link>
        <p className="text-red-400 text-sm mt-2">{error}</p>
      </div>
    )
  }

  if (!job) return null

  const fs = job.findings_summary

  return (
    <div className="p-8 max-w-3xl">
      {/* Breadcrumb + title */}
      <Link
        href="/dashboard"
        className="text-xs text-zinc-500 hover:text-zinc-300 mb-4 inline-block transition-colors"
      >
        ← Back to audits
      </Link>

      <div className="flex items-start gap-3 mb-2 flex-wrap">
        <h1 className="font-mono text-base font-bold text-zinc-100 break-all">
          {job.job_id}
        </h1>
        <div className="flex items-center gap-2 shrink-0">
          <StatusBadge status={job.status} />
          <CopyButton text={job.job_id} />
        </div>
      </div>

      {/* Polling indicator */}
      {ACTIVE.has(job.status) && (
        <div className="text-xs text-cyan-400 font-mono bg-cyan-400/5 border border-cyan-400/20 rounded px-3 py-2 mb-4 flex items-center gap-2">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
          Polling every 5s…
        </div>
      )}

      {/* Metadata */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5 mb-4">
        <h2 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">
          Details
        </h2>
        <dl className="grid grid-cols-2 gap-y-2.5 text-sm">
          <DetailRow label="Type" value={job.input_type} />
          <DetailRow label="Created" value={job.created_at.slice(0, 19).replace('T', ' ')} />
          {job.started_at && (
            <DetailRow label="Started" value={job.started_at.slice(0, 19).replace('T', ' ')} />
          )}
          {job.completed_at && (
            <DetailRow label="Completed" value={job.completed_at.slice(0, 19).replace('T', ' ')} />
          )}
          {job.started_at && job.completed_at && (
            <DetailRow
              label="Duration"
              value={`${(
                (new Date(job.completed_at).getTime() -
                  new Date(job.started_at).getTime()) /
                1000
              ).toFixed(1)}s`}
            />
          )}
          {job.error_message && (
            <DetailRow
              label="Error"
              value={job.error_message}
              className="text-red-400 break-all"
            />
          )}
        </dl>
      </div>

      {/* Findings summary */}
      {fs && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5 mb-4">
          <h2 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">
            Findings
          </h2>
          <div className="flex gap-2 flex-wrap">
            <SeverityBadge level="critical" count={fs.critical} />
            <SeverityBadge level="high" count={fs.high} />
            <SeverityBadge level="medium" count={fs.medium} />
            <SeverityBadge level="low" count={fs.low} />
            <span className="text-xs text-zinc-500 self-center ml-1">
              {fs.total} total
            </span>
          </div>
          {fs.critical > 0 && (
            <p className="text-xs text-red-400 mt-3">
              {fs.critical} critical finding{fs.critical > 1 ? 's' : ''} detected — download the full report for details.
            </p>
          )}
        </div>
      )}

      {/* Artifacts */}
      {artifacts.length > 0 && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <h2 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">
            Artifacts
          </h2>
          <ul>
            {artifacts.map((art) => (
              <li
                key={art.name}
                className="flex items-center justify-between py-2.5 border-b border-zinc-800 last:border-0"
              >
                <div className="min-w-0">
                  <span className="text-sm font-mono text-zinc-100">
                    {art.name}
                  </span>
                  <span className="text-xs text-zinc-500 ml-2">
                    {(art.size_bytes / 1024).toFixed(1)} KB
                  </span>
                </div>
                {art.presigned_url ? (
                  <a
                    href={art.presigned_url}
                    download={art.name}
                    className="shrink-0 ml-4 text-xs text-cyan-400 hover:text-cyan-300 border border-cyan-400/20 hover:border-cyan-400/40 px-3 py-1 rounded transition-colors"
                  >
                    Download
                  </a>
                ) : (
                  <span className="shrink-0 ml-4 text-xs text-zinc-600">
                    Local only
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Running: no artifacts yet */}
      {ACTIVE.has(job.status) && artifacts.length === 0 && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5 text-center">
          <p className="text-sm text-zinc-500">
            Artifacts will appear here once the audit completes.
          </p>
        </div>
      )}
    </div>
  )
}
