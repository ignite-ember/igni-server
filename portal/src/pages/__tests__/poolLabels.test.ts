/**
 * F132. A node's pools were rendered straight from ``node.pools`` in
 * four places, which is whatever order the API sent — so the inference
 * list showed one row reading "CodeIndex · Chat" above two reading
 * "Chat · CodeIndex". Nothing was wrong with any single row; the page
 * just looked unmaintained.
 */
import { describe, expect, it } from 'vitest'
import { poolLabels } from '../pools'

describe('poolLabels', () => {
  it('reads the same whichever order the API sent', () => {
    expect(poolLabels(['chat', 'codeindex'])).toEqual(['Chat', 'CodeIndex'])
    expect(poolLabels(['codeindex', 'chat'])).toEqual(['Chat', 'CodeIndex'])
  })

  it('titles the pools it knows', () => {
    expect(poolLabels(['codeindex'])).toEqual(['CodeIndex'])
  })

  it('puts a pool it has no title for last, under its raw name', () => {
    // A name from a newer server than this portal. Showing it is right;
    // leading with it is not.
    expect(poolLabels(['embeddings', 'chat'])).toEqual(['Chat', 'embeddings'])
  })

  it('does not mutate what it was given', () => {
    const pools = ['codeindex', 'chat']
    poolLabels(pools)
    expect(pools).toEqual(['codeindex', 'chat'])
  })
})
