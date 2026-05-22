'use client'

import { useState } from 'react'

interface CopyButtonProps {
  text: string
  className?: string
}

export function CopyButton({ text, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard API unavailable (non-HTTPS or unsupported browser)
    }
  }

  return (
    <button
      onClick={handleCopy}
      className={
        className ??
        'text-xs text-zinc-500 hover:text-zinc-200 transition-colors px-1.5 py-0.5 rounded hover:bg-zinc-700'
      }
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}
