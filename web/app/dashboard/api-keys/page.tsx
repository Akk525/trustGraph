'use client'

import { useState, useEffect } from 'react'
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  ApiKey,
  ApiKeyCreated,
  ApiError,
} from '@/lib/api'
import { CopyButton } from '@/components/CopyButton'
import { ConfirmModal } from '@/components/ConfirmModal'

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState<ApiKeyCreated | null>(null)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null)

  async function load() {
    try {
      const ks = await listApiKeys()
      setKeys(ks)
    } catch (err) {
      if (err instanceof ApiError && err.status !== 401) {
        setError(err.message)
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    setError(null)
    try {
      const created = await createApiKey(newName.trim())
      setNewKey(created)
      setNewName('')
      setLoading(true)
      await load()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Failed to create API key.',
      )
    } finally {
      setCreating(false)
    }
  }

  async function handleRevoke(keyId: string) {
    setRevoking(keyId)
    try {
      await revokeApiKey(keyId)
      setLoading(true)
      await load()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Failed to revoke API key.',
      )
    } finally {
      setRevoking(null)
    }
  }

  const activeKeys = keys.filter((k) => !k.revoked_at)

  return (
    <div className="p-8 max-w-2xl">
      <h1 className="text-xl font-bold text-zinc-100 mb-6">API keys</h1>

      {/* Confirm revoke modal */}
      {confirmRevoke && (
        <ConfirmModal
          title="Revoke API key"
          message="This key will be invalidated immediately. Any integrations using it will stop working. This cannot be undone."
          confirmLabel="Revoke key"
          danger
          onConfirm={() => {
            handleRevoke(confirmRevoke)
            setConfirmRevoke(null)
          }}
          onCancel={() => setConfirmRevoke(null)}
        />
      )}

      {/* One-time raw key banner */}
      {newKey && (
        <div className="bg-green-400/10 border border-green-400/30 rounded-lg p-4 mb-6">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs text-green-400 font-semibold">
              Key created — save it now. It will not be shown again.
            </p>
            <CopyButton
              text={newKey.raw_key}
              className="text-xs text-green-400 hover:text-green-300 border border-green-400/30 px-2 py-0.5 rounded transition-colors"
            />
          </div>
          <code className="block font-mono text-sm text-green-300 break-all bg-zinc-900/60 rounded px-3 py-2">
            {newKey.raw_key}
          </code>
          <button
            onClick={() => setNewKey(null)}
            className="text-xs text-zinc-500 hover:text-zinc-300 mt-2 transition-colors"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Create form */}
      <form
        onSubmit={handleCreate}
        className="bg-zinc-900 border border-zinc-800 rounded-lg p-5 mb-6"
      >
        <h2 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">
          Create API key
        </h2>
        <div className="flex gap-3">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Key name — e.g. CI pipeline"
            className="flex-1 min-w-0 bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:border-cyan-500 placeholder-zinc-600 transition-colors"
          />
          <button
            type="submit"
            disabled={!newName.trim() || creating}
            className="shrink-0 bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 disabled:cursor-not-allowed text-zinc-950 font-semibold text-sm px-4 py-2 rounded transition-colors"
          >
            {creating ? 'Creating…' : 'Create'}
          </button>
        </div>
      </form>

      {error && (
        <div className="text-red-400 text-sm bg-red-400/10 border border-red-400/20 rounded px-3 py-2 mb-4">
          {error}
        </div>
      )}

      {/* Keys list */}
      {loading ? (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => (
            <div
              key={i}
              className="h-10 bg-zinc-800 rounded animate-pulse"
            />
          ))}
        </div>
      ) : activeKeys.length === 0 ? (
        <div className="text-zinc-500 text-sm py-4 text-center bg-zinc-900 border border-zinc-800 rounded-lg">
          No active API keys. Create one above.
        </div>
      ) : (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800">
                <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal">
                  Name
                </th>
                <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal">
                  Prefix
                </th>
                <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal hidden sm:table-cell">
                  Created
                </th>
                <th className="text-left px-4 py-3 text-xs text-zinc-500 font-normal hidden md:table-cell">
                  Last used
                </th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {activeKeys.map((k) => (
                <tr
                  key={k.key_id}
                  className="border-b border-zinc-800/50 last:border-0"
                >
                  <td className="px-4 py-3 text-zinc-100">{k.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-400">
                    {k.key_prefix}…
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-400 hidden sm:table-cell">
                    {k.created_at.slice(0, 10)}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-400 hidden md:table-cell">
                    {k.last_used_at ? k.last_used_at.slice(0, 10) : '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => setConfirmRevoke(k.key_id)}
                      disabled={revoking === k.key_id}
                      className="text-xs text-red-400 hover:text-red-300 border border-red-400/20 hover:border-red-400/40 px-2 py-1 rounded transition-colors disabled:opacity-50"
                    >
                      {revoking === k.key_id ? 'Revoking…' : 'Revoke'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Usage note */}
      <div className="mt-6 bg-zinc-900 border border-zinc-800 rounded-lg p-4 space-y-3">
        <h2 className="text-xs text-zinc-500 uppercase tracking-wider">
          Usage
        </h2>
        <div>
          <p className="text-xs text-zinc-500 mb-1.5">HTTP header</p>
          <div className="flex items-center justify-between bg-zinc-800 rounded px-3 py-2">
            <code className="font-mono text-xs text-zinc-300">
              Authorization: Bearer tg_xxxxxxxxxxxx
            </code>
            <CopyButton text="Authorization: Bearer tg_xxxxxxxxxxxx" />
          </div>
        </div>
        <div>
          <p className="text-xs text-zinc-500 mb-1.5">CLI</p>
          <div className="flex items-center justify-between bg-zinc-800 rounded px-3 py-2">
            <code className="font-mono text-xs text-zinc-300">
              TRUSTGRAPH_API_KEY=tg_xxxxxxxxxxxx trustgraph-cloud jobs
            </code>
            <CopyButton text="TRUSTGRAPH_API_KEY=tg_xxxxxxxxxxxx trustgraph-cloud jobs" />
          </div>
        </div>
      </div>
    </div>
  )
}
