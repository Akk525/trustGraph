const BASE_URL = (
  process.env.NEXT_PUBLIC_TRUSTGRAPH_API_URL ?? ''
).replace(/\/$/, '')

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function storedToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem('tg_token')
}

function authHeaders(): Record<string, string> {
  const t = storedToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!BASE_URL && typeof window !== 'undefined') {
    console.warn(
      '[TrustGraph] NEXT_PUBLIC_TRUSTGRAPH_API_URL is not set — API calls will fail. ' +
        'Copy .env.example to .env.local and set the variable.',
    )
  }
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init.headers as Record<string, string> | undefined),
    },
  })

  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('tg_token')
      window.location.href = '/login'
    }
    throw new ApiError(401, 'Unauthorized')
  }

  if (res.status === 204) return undefined as unknown as T

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body.detail) detail = String(body.detail)
    } catch {
      // ignore JSON parse error
    }
    throw new ApiError(res.status, detail)
  }

  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface FindingsSummary {
  critical: number
  high: number
  medium: number
  low: number
  total: number
}

export interface Job {
  job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  created_at: string
  started_at: string | null
  completed_at: string | null
  input_type: string
  findings_summary: FindingsSummary | null
  artifact_names: string[]
  artifact_count: number
  error_message: string | null
}

export interface AuditListResponse {
  jobs: Job[]
  total: number | null
  limit: number
  offset: number | null
  has_more: boolean
  next_cursor: string | null
}

export interface Artifact {
  name: string
  size_bytes: number
  storage_backend: string
  path?: string | null
  s3_key?: string | null
  presigned_url?: string | null
  content_type?: string | null
}

export interface ArtifactsResponse {
  job_id: string
  artifacts: Artifact[]
}

export interface ApiKey {
  key_id: string
  name: string
  key_prefix: string
  created_at: string
  last_used_at?: string | null
  revoked_at?: string | null
}

export interface ApiKeyCreated extends ApiKey {
  raw_key: string
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function login(
  email: string,
  password: string,
): Promise<{ access_token: string; expires_in: number }> {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function signup(
  email: string,
  password: string,
): Promise<{ access_token: string; expires_in: number }> {
  return request('/auth/signup', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export interface ListJobsParams {
  limit?: number
  cursor?: string
  offset?: number
}

export async function listJobs(
  params: ListJobsParams = {},
): Promise<AuditListResponse> {
  const q = new URLSearchParams()
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.cursor) q.set('cursor', params.cursor)
  if (params.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString() ? `?${q}` : ''
  return request(`/audits${qs}`)
}

export async function getJob(jobId: string): Promise<Job> {
  return request(`/audits/${encodeURIComponent(jobId)}`)
}

export async function submitAudit(inputS3Key: string): Promise<Job> {
  return request('/audits', {
    method: 'POST',
    body: JSON.stringify({ input_s3_key: inputS3Key }),
  })
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

export async function listArtifacts(jobId: string): Promise<ArtifactsResponse> {
  return request(`/audits/${encodeURIComponent(jobId)}/artifacts`)
}

// ---------------------------------------------------------------------------
// Uploads
// ---------------------------------------------------------------------------

export async function presignedUpload(
  filename: string,
  contentType = 'application/zip',
): Promise<{ upload_url: string; input_s3_key: string; expires_in: number }> {
  return request('/uploads/presigned', {
    method: 'POST',
    body: JSON.stringify({ filename, content_type: contentType }),
  })
}

export async function uploadToS3(
  uploadUrl: string,
  file: File | Blob,
): Promise<void> {
  const res = await fetch(uploadUrl, {
    method: 'PUT',
    body: file,
    headers: { 'Content-Type': file.type || 'application/zip' },
  })
  if (!res.ok) {
    throw new ApiError(res.status, `S3 upload failed: ${res.statusText}`)
  }
}

// ---------------------------------------------------------------------------
// API keys
// ---------------------------------------------------------------------------

export async function listApiKeys(): Promise<ApiKey[]> {
  return request('/api-keys')
}

export async function createApiKey(name: string): Promise<ApiKeyCreated> {
  return request('/api-keys', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function revokeApiKey(keyId: string): Promise<void> {
  return request(`/api-keys/${encodeURIComponent(keyId)}`, {
    method: 'DELETE',
  })
}
