export const ALLOWED_AUDIT_EXTENSIONS = ['.zip', '.sol']

/**
 * Returns an error string if the filename is not an accepted audit format,
 * null if valid. Operates on the filename string so it can be unit-tested
 * without a File object.
 */
export function validateAuditFileName(name: string): string | null {
  const lower = name.toLowerCase()
  const ok = ALLOWED_AUDIT_EXTENSIONS.some((ext) => lower.endsWith(ext))
  if (!ok) {
    const got = name.includes('.') ? name.slice(name.lastIndexOf('.')) : name
    return `Only .zip and .sol files are accepted (got: ${got})`
  }
  return null
}
