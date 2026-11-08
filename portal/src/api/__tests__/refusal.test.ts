import { describe, expect, test } from 'vitest'
import { formRefusalOf, refusalOf } from '../refusal'

const refused = (detail: unknown) => ({ response: { data: { detail } } })

describe('refusalOf', () => {
  test('shows the server sentence', () => {
    expect(refusalOf(refused('No.'), 'fallback')).toBe('No.')
  })

  test('falls back for anything that is not a sentence', () => {
    expect(refusalOf(refused([{ msg: 'x' }]), 'fallback')).toBe('fallback')
    expect(refusalOf(new Error('network'), 'fallback')).toBe('fallback')
    expect(refusalOf(null, 'fallback')).toBe('fallback')
  })
})

describe('formRefusalOf', () => {
  test('shows the server sentence', () => {
    expect(formRefusalOf(refused('No.'), 'fallback')).toBe('No.')
  })

  test('joins the messages of a rejected body', () => {
    expect(formRefusalOf(refused([{ msg: 'One' }, { msg: '' }, { msg: 'Two' }]), 'fallback')).toBe('One. Two')
  })

  test('falls back when the list carries no message', () => {
    expect(formRefusalOf(refused([]), 'fallback')).toBe('fallback')
    expect(formRefusalOf(refused([{}, null]), 'fallback')).toBe('fallback')
  })
})
