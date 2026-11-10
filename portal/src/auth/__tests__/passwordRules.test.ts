import { describe, expect, test } from 'vitest'
import { missingPasswordRules } from '../passwordRules'

describe('missingPasswordRules', () => {
  test('an empty password reports nothing yet', () => {
    expect(missingPasswordRules('')).toEqual([])
  })

  test('a strong password breaks no rule', () => {
    expect(missingPasswordRules('Str0ng!Pass')).toEqual([])
  })

  test('names each broken rule', () => {
    expect(missingPasswordRules('abc')).toEqual([
      'At least 8 characters',
      'One uppercase letter',
      'One digit',
      'One special character',
    ])
  })
})
