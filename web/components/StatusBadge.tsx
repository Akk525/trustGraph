const CLASSES: Record<string, string> = {
  queued: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20',
  running: 'text-cyan-400 bg-cyan-400/10 border-cyan-400/20',
  succeeded: 'text-green-400 bg-green-400/10 border-green-400/20',
  failed: 'text-red-400 bg-red-400/10 border-red-400/20',
}

export function StatusBadge({ status }: { status: string }) {
  const cls =
    CLASSES[status] ?? 'text-zinc-400 bg-zinc-400/10 border-zinc-400/20'
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono border ${cls}`}
    >
      {status}
    </span>
  )
}
