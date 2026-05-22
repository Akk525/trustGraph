'use client'

import { useEffect } from 'react'
import Link from 'next/link'
import { useRouter, usePathname } from 'next/navigation'
import { getToken, clearToken } from '@/lib/auth'

function NavItem({ href, label }: { href: string; label: string }) {
  const pathname = usePathname()
  const active = pathname === href || (href !== '/dashboard' && pathname.startsWith(href))
  return (
    <Link
      href={href}
      className={`flex items-center px-3 py-2 rounded text-sm transition-colors ${
        active
          ? 'bg-zinc-800 text-zinc-100'
          : 'text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/50'
      }`}
    >
      {label}
    </Link>
  )
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const router = useRouter()

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login')
    }
  }, [router])

  function handleLogout() {
    clearToken()
    router.push('/login')
  }

  return (
    <div className="min-h-screen bg-zinc-950 flex">
      {/* Sidebar */}
      <aside className="w-52 border-r border-zinc-800 flex flex-col shrink-0">
        <div className="px-4 py-5 border-b border-zinc-800">
          <Link
            href="/"
            className="font-mono text-sm font-bold text-cyan-400 hover:text-cyan-300 transition-colors"
          >
            TrustGraph
          </Link>
        </div>

        <nav className="flex-1 px-2 py-4 space-y-0.5">
          <NavItem href="/dashboard" label="Audits" />
          <NavItem href="/dashboard/new" label="New audit" />
          <NavItem href="/dashboard/api-keys" label="API keys" />
        </nav>

        <div className="px-2 py-4 border-t border-zinc-800">
          <button
            onClick={handleLogout}
            className="w-full text-left px-3 py-2 text-sm text-zinc-500 hover:text-zinc-100 transition-colors rounded hover:bg-zinc-800/50"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-auto min-w-0">{children}</main>
    </div>
  )
}
