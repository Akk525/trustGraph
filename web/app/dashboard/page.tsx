'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { listJobs, Job, ApiError } from '@/lib/api'
import { StatusBadge } from '@/components/StatusBadge'
import { KpiCard } from '@/components/KpiCard'

const ACTIVE = new Set<string>(['queued', 'running'])
const LIMIT = 20
const POLL_MS = 5000

const STATUS_OPTIONS = [
  { value: 'all', label: 'All statuses' },
  { value: 'queued', label: 'Queued' },
  { value: 'running', label: 'Running' },
  { value: 'succeeded', label: 'Succeeded' },
  { value: 'failed', label: 'Failed' },
]

function SkeletonRow() {
  return (
    <tr className="border-b border-zinc-800/50">
      {[...Array(5)].map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-3 bg-zinc-800 rounded animate-pulse" />
        </td>
      ))}
    </tr>
  )
}

export default function DashboardPage() {
  const router = useRouter()
  const [jobs, setJobs] = useState<Job[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [total, setTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('all')

  const loadPage = useCallback(async (cursor?: string) => {
    const data = await listJobs({ limit: LIMIT, cursor })
    if (cursor) {
      setJobs((prev) => [...prev, ...data.jobs])
    } else {
      setJobs(data.jobs)
      setTotal(data.total)
    }
    setNextCursor(data.next_cursor)
    setHasMore(data.has_more)
  }, [])

  function initialLoad() {
    setLoading(true)
    setError(null)
    loadPage()
      .catch((err) => {
        if (err instanceof ApiError && err.status !== 401) {
          setError(err.message)
        }
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    initialLoad()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadPage])

  // Poll when any visible job is still active
  useEffect(() => {
    const hasActive = jobs.some((j) => ACTIVE.has(j.status))
    if (!hasActive) return

    const id = setInterval(async () => {
      try {
        const fresh = await listJobs({ limit: Math.max(jobs.length, LIMIT) })
        setJobs(fresh.jobs)
        setTotal(fresh.total)
        setNextCursor(fresh.next_cursor)
        setHasMore(fresh.has_more)
      } catch {
        // Ignore poll errors; retry on next tick
      }
    }, POLL_MS)

    return () => clearInterval(id)
  }, [jobs])

  async function loadMore() {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    try {
      await loadPage(nextCursor)
    } catch (err) {
      if (err instanceof ApiError && err.status !== 401) {
        setError(err.message)
      }
    } finally {
      setLoadingMore(false)
    }
  }

  const activeCount = jobs.filter((j) => ACTIVE.has(j.status)).length
  const failedCount = jobs.filter((j) => j.status === 'failed').length
  const criticalCount = jobs.reduce(
    (n, j) => n + (j.findings_summary?.critical ?? 0),
    0,
  )
  const totalDisplay =
    total != null ? total : `${jobs.length}${hasMore ? '+' : ''}`

  const filteredJobs =
    statusFilter === 'all'
      ? jobs
      : jobs.filter((j) => j.status === statusFilter)

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-zinc-100">Audits</h1>
        <Link
          href="/dashboard/new"
          className="bg-cyan-500 hover:bg-cyan-400 text-zinc-950 font-semibold text-sm px-4 py-2 rounded transition-colors"
        >
          + New audit
        </Link>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <KpiCard label="Total jobs" value={totalDisplay} />
        <KpiCard
          label="Active"
          value={activeCount}
          sub={activeCount > 0 ? 'polling every 5s' : undefined}
        />
        <KpiCard label="Failed" value={failedCount} />
        <KpiCard label="Critical findings" value={criticalCount} />
      </div>

      {/* Network error banner */}
      {error && (
        <div className="text-red-400 text-sm bg-red-400/10 border border-red-400/20 rounded px-4 py-3 mb-4 flex items-center justify-between gap-4">
          <span>{error}</span>
          <button
            onClick={initialLoad}
            className="shrink-0 text-xs text-zinc-300 hover:text-zinc-100 border border-zinc-600 hover:border-zinc-400 px-3 py-1 rounded transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Table toolbar */}
      {jobs.length > 0 && (
        <div className="flex items-center gap-3 mb-3">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 text-xs text-zinc-300 rounded px-2 py-1.5 focus:outline-none focus:border-cyan-500 transition-colors"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {statusFilter !== 'all' && (
            <button
              onClick={() => setStatusFilter('all')}
              className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              Clear
            </button>
          )}
          <span className="text-xs text-zinc-600 ml-auto">
            {filteredJobs.length} job{filteredJobs.length !== 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <tbody>
              {[...Array(5)].map((_, i) => (
                <SkeletonRow key={i} />
              ))}
            </tbody>
          </table>
        </div>
      ) : filteredJobs.length === 0 && jobs.length === 0 ? (
        <div className="text-center py-20 bg-zinc-900 border border-zinc-800 rounded-lg">
          <p className="text-zinc-400 font-medium mb-1">No audits yet</p>
          <p className="text-zinc-500 text-sm mb-4">
            Submit your first Solidity project to get started.
          </p>
          <Link
            href="/dashboard/new"
            className="inline-block bg-cyan-500 hover:bg-cyan-400 text-zinc-950 font-semibold text-sm px-5 py-2 rounded transition-colors"
          >
            Run first audit →
          </Link>
        </div>
      ) : filteredJobs.length === 0 ? (
        <div className="text-center py-10 text-zinc-500 text-sm">
          No jobs match the current filter.{' '}
          <button
            onClick={() => setStatusFilter('all')}
            className="text-cyan-400 hover:text-cyan-300"
          >
            Show all
          </button>
        </div>
      ) : (
        <>
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-800">
                  <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal">
                    Job ID
                  </th>
                  <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal">
                    Status
                  </th>
                  <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal hidden sm:table-cell">
                    Type
                  </th>
                  <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal hidden md:table-cell">
                    Created
                  </th>
                  <th className="text-right px-4 py-3 text-xs text-zinc-500 font-normal">
                    Findings
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map((job) => (
                  <tr
                    key={job.job_id}
                    onClick={() =>
                      router.push(`/dashboard/audits/${job.job_id}`)
                    }
                    className="border-b border-zinc-800/50 hover:bg-zinc-800/40 transition-colors cursor-pointer"
                  >
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs text-cyan-400">
                        {job.job_id.slice(0, 8)}…
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={job.status} />
                    </td>
                    <td className="px-4 py-3 text-zinc-400 text-xs hidden sm:table-cell">
                      {job.input_type}
                    </td>
                    <td className="px-4 py-3 text-zinc-400 text-xs font-mono hidden md:table-cell">
                      {job.created_at.slice(0, 19).replace('T', ' ')}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {job.findings_summary ? (
                        <span className="text-xs font-mono">
                          <span className="text-red-400">
                            {job.findings_summary.critical}c
                          </span>{' '}
                          <span className="text-yellow-400">
                            {job.findings_summary.medium}m
                          </span>{' '}
                          <span className="text-zinc-400">
                            {job.findings_summary.total}t
                          </span>
                        </span>
                      ) : (
                        <span className="text-zinc-600 text-xs">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {hasMore && statusFilter === 'all' && (
            <div className="mt-4 text-center">
              <button
                onClick={loadMore}
                disabled={loadingMore}
                className="text-sm text-zinc-400 hover:text-zinc-100 border border-zinc-700 hover:border-zinc-500 px-4 py-2 rounded transition-colors disabled:opacity-50"
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
