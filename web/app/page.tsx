import Link from 'next/link'

const FEATURES = [
  {
    title: 'Static analysis',
    desc: 'Regex-based predicate scan detects trust violations deterministically. No LLM hallucination on severity.',
  },
  {
    title: 'AI enrichment',
    desc: 'Gemini adds assumptions and patch recommendations — subordinate to the scanner, never overriding severity.',
  },
  {
    title: 'Cloud scale',
    desc: 'Fargate workers, SQS queuing, DynamoDB state. Cursor-paginated job history. Presigned S3 uploads.',
  },
]

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Nav */}
      <nav className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between">
        <span className="font-mono text-base font-bold text-cyan-400">
          TrustGraph
        </span>
        <div className="flex items-center gap-3">
          <Link
            href="/login"
            className="text-sm text-zinc-400 hover:text-zinc-100 transition-colors"
          >
            Sign in
          </Link>
          <Link
            href="/signup"
            className="text-sm bg-cyan-500 hover:bg-cyan-400 text-zinc-950 font-semibold px-4 py-1.5 rounded transition-colors"
          >
            Get started
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <main className="max-w-4xl mx-auto px-6 py-24">
        <div className="mb-4">
          <span className="text-xs font-mono text-cyan-400 bg-cyan-400/10 border border-cyan-400/20 px-2 py-1 rounded">
            E/P/V/G predicate scoring
          </span>
        </div>

        <h1 className="text-5xl font-bold text-zinc-100 leading-tight mb-6">
          Trust-boundary analysis
          <br />
          <span className="text-cyan-400">for Solidity.</span>
        </h1>

        <p className="text-lg text-zinc-400 mb-8 max-w-2xl">
          Detect CrossCurve-style vulnerabilities with deterministic scoring.
          Submit a ZIP, get a structured findings report with Foundry exploit stubs.
        </p>

        <div className="flex gap-4">
          <Link
            href="/signup"
            className="bg-cyan-500 hover:bg-cyan-400 text-zinc-950 font-semibold px-6 py-3 rounded transition-colors"
          >
            Start auditing →
          </Link>
          <Link
            href="/login"
            className="border border-zinc-700 hover:border-zinc-500 text-zinc-300 hover:text-zinc-100 px-6 py-3 rounded transition-colors"
          >
            Sign in
          </Link>
        </div>

        {/* Features */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-20">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="bg-zinc-900 border border-zinc-800 rounded-lg p-5"
            >
              <h3 className="font-mono text-sm font-semibold text-cyan-400 mb-2">
                {f.title}
              </h3>
              <p className="text-sm text-zinc-400">{f.desc}</p>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
