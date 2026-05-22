import { ApiError } from '../lib/api'

describe('ApiError', () => {
  it('is an instance of Error', () => {
    const err = new ApiError(404, 'Not found')
    expect(err).toBeInstanceOf(Error)
  })

  it('exposes the HTTP status code', () => {
    expect(new ApiError(503, 'Unavailable').status).toBe(503)
    expect(new ApiError(401, 'Unauthorized').status).toBe(401)
  })

  it('message matches the detail string', () => {
    const err = new ApiError(400, 'Provide cursor or offset, not both')
    expect(err.message).toBe('Provide cursor or offset, not both')
  })

  it('name is ApiError', () => {
    expect(new ApiError(500, 'Server error').name).toBe('ApiError')
  })
})
