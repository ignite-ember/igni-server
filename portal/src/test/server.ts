/**
 * Process-wide MSW server. Tests import this and call `server.use(...)`
 * to entry default responses. Reset between tests so per-test
 * entries don't leak.
 */
import { setupServer } from 'msw/node'
import { defaultHandlers } from './handlers'

export const server = setupServer(...defaultHandlers)
