import { validateAuditFileName, ALLOWED_AUDIT_EXTENSIONS } from '../lib/utils'

describe('validateAuditFileName', () => {
  it('accepts .zip files', () => {
    expect(validateAuditFileName('project.zip')).toBeNull()
  })

  it('accepts .sol files', () => {
    expect(validateAuditFileName('Contract.sol')).toBeNull()
  })

  it('accepts case-insensitive extensions', () => {
    expect(validateAuditFileName('Project.ZIP')).toBeNull()
    expect(validateAuditFileName('Token.SOL')).toBeNull()
  })

  it('rejects .txt files', () => {
    const err = validateAuditFileName('notes.txt')
    expect(err).not.toBeNull()
    expect(err).toContain('.txt')
  })

  it('rejects .js files', () => {
    expect(validateAuditFileName('index.js')).not.toBeNull()
  })

  it('rejects files with no extension', () => {
    expect(validateAuditFileName('Makefile')).not.toBeNull()
  })

  it('ALLOWED_AUDIT_EXTENSIONS contains .zip and .sol', () => {
    expect(ALLOWED_AUDIT_EXTENSIONS).toContain('.zip')
    expect(ALLOWED_AUDIT_EXTENSIONS).toContain('.sol')
  })
})
