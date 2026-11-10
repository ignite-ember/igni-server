import { describe, expect, it } from 'vitest'
import { KIND_BY_ID } from '../entryKinds'
import { checkNewEntry, INITIAL_NEW_ENTRY_FORM, withKind, type NewEntryForm } from '../newEntryForm'

const plugin = (url: string, ref = '', subdir = ''): NewEntryForm => ({
  ...withKind(INITIAL_NEW_ENTRY_FORM, 'plugins'),
  entryName: 'p',
  pluginSource: { url, ref, subdir },
})

describe('withKind', () => {
  it('loads the new kind’s template', () => {
    expect(withKind(INITIAL_NEW_ENTRY_FORM, 'mcps').content).toBe(KIND_BY_ID.mcps.template)
  })

  it('drops the model on leaving agents', () => {
    expect(withKind({ ...INITIAL_NEW_ENTRY_FORM, model: 'm' }, 'skills').model).toBeNull()
  })

  it('drops the source on leaving plugins', () => {
    expect(withKind(plugin('https://x/y', 'main'), 'agents').pluginSource).toEqual({ url: '', ref: '', subdir: '' })
  })

  it('leaves the form alone when the kind does not change', () => {
    const form = { ...INITIAL_NEW_ENTRY_FORM, content: 'edited' }
    expect(withKind(form, 'agents')).toBe(form)
  })
})

describe('checkNewEntry', () => {
  it('wants a name', () => {
    expect(checkNewEntry({ ...INITIAL_NEW_ENTRY_FORM, entryName: '  ' })).toEqual({
      ok: false,
      field: 'name',
      message: 'Entry name is required',
    })
  })

  it('refuses a scheme the server would', () => {
    expect(checkNewEntry(plugin('file:///etc'))).toMatchObject({ ok: false, field: 'sourceUrl' })
  })

  it('ignores a ref with no URL, and wants content then', () => {
    expect(checkNewEntry({ ...plugin('', 'main'), content: '' })).toMatchObject({ ok: false, field: 'content' })
  })

  it('needs no content for a plugin from git', () => {
    const check = checkNewEntry({ ...plugin(' https://x/y ', ' main ', ''), content: '' })
    expect(check).toMatchObject({
      ok: true,
      request: { source_url: 'https://x/y', source_ref: 'main', source_subdir: null },
    })
  })

  it('sends the model only for an agent that names one', () => {
    const named = checkNewEntry({ ...INITIAL_NEW_ENTRY_FORM, entryName: 'a', model: 'm' })
    const unnamed = checkNewEntry({ ...INITIAL_NEW_ENTRY_FORM, entryName: 'a' })
    expect(named.ok && named.request.model).toBe('m')
    expect(unnamed.ok && 'model' in unnamed.request).toBe(false)
  })
})
