# TrustGraph Web Dashboard

Next.js 14 App Router frontend for TrustGraph Cloud. Dark security-tool
aesthetic; cursor-paginated job list with 5s polling for active jobs.

## Quick start

```bash
cd web
npm install
cp .env.example .env.local   # set NEXT_PUBLIC_TRUSTGRAPH_API_URL
npm run dev                  # http://localhost:3000
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `NEXT_PUBLIC_TRUSTGRAPH_API_URL` | **Yes** | TrustGraph Cloud API base URL (no trailing slash) |

If this variable is not set, all API calls will fail and the browser console
will show a warning.

### Public (production) API

```
NEXT_PUBLIC_TRUSTGRAPH_API_URL=http://trustgraph-api-1082070420.us-east-1.elb.amazonaws.com
```

### Local API

```
NEXT_PUBLIC_TRUSTGRAPH_API_URL=http://localhost:8000
```

## Commands

```bash
npm run dev      # Start development server (http://localhost:3000)
npm run build    # Production build (also runs type-check)
npm run lint     # ESLint
npm test         # Jest unit tests
```

## Deploy to Vercel

1. Import the repository into [Vercel](https://vercel.com)
2. Set **Root Directory** to `web`
3. Add environment variable `NEXT_PUBLIC_TRUSTGRAPH_API_URL`
4. Click Deploy — Vercel auto-detects Next.js and runs `npm run build`

The `vercel.json` in this directory sets `"framework": "nextjs"` explicitly.

## Project structure

```
web/
├── app/
│   ├── layout.tsx                     root layout
│   ├── page.tsx                       landing /
│   ├── login/page.tsx
│   ├── signup/page.tsx
│   └── dashboard/
│       ├── layout.tsx                 sidebar nav + auth guard
│       ├── page.tsx                   job list (cursor pagination, 5s poll)
│       ├── new/page.tsx               presigned S3 upload + submit
│       ├── audits/[jobId]/page.tsx    job detail + artifact downloads
│       └── api-keys/page.tsx          create / list / revoke API keys
├── components/
│   ├── ConfirmModal.tsx               reusable confirm dialog
│   ├── CopyButton.tsx                 one-click clipboard copy
│   ├── KpiCard.tsx                    metric card
│   └── StatusBadge.tsx                job status coloured badge
├── lib/
│   ├── api.ts                         typed fetch wrapper (all API calls)
│   ├── auth.ts                        localStorage token helpers (SSR-safe)
│   └── utils.ts                       file validation and shared helpers
└── __tests__/
    ├── auth.test.ts
    ├── file-validation.test.ts
    └── api-error.test.ts
```

## Auth flow

- Login / signup stores the JWT as `tg_token` in `localStorage`
- Every request sends `Authorization: Bearer <token>`
- HTTP 401 → token cleared → redirected to `/login`
- Dashboard layout checks for token on mount and redirects unauthenticated visitors

## CORS setup

The API server must be configured to allow requests from the browser origin.
Set `TRUSTGRAPH_CORS_ORIGINS` in the API environment before deploying the frontend.

### Local dev

```bash
# Start API with CORS for local Next.js dev server
TRUSTGRAPH_CORS_ORIGINS="http://localhost:3000" uvicorn trustgraph_cloud.api.main:app --reload
```

### Production (CDK)

```bash
cd infra/cdk
cdk deploy --context cors_origins="https://your-app.vercel.app"
```

See [`docs/phase5c-cors-deploy.md`](../docs/phase5c-cors-deploy.md) for the full
deployment guide, CDK context options, smoke tests, and debugging steps.
