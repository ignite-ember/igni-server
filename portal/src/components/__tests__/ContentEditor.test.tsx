/**
 * The entry editor's expand toggle.
 *
 * An agent prompt runs to hundreds of lines and a workflow is a
 * program; both were written through a 380px window. Expanding covers
 * the viewport, so the way back matters as much as the way in — there
 * is a Collapse button inside the expanded view and Escape works too.
 */
import { afterEach, describe, expect, test } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ContentEditor } from '../ContentEditor'

afterEach(cleanup)

function renderEditor(props: Partial<Parameters<typeof ContentEditor>[0]> = {}) {
  return render(
    <ContentEditor value="hello" onChange={() => {}} contentType="markdown" {...props} />,
  )
}

describe('expanding', () => {
  test('starts inline', () => {
    const { container } = renderEditor()

    expect(container.querySelector('.content-editor--expanded')).toBeNull()
    expect(screen.getByRole('button', { name: /expand/i })).toBeInTheDocument()
  })

  test('expand takes over the viewport', async () => {
    const { container } = renderEditor()

    await userEvent.click(screen.getByRole('button', { name: /expand/i }))

    expect(container.querySelector('.content-editor--expanded')).not.toBeNull()
  })

  test('and collapses back to where it was', async () => {
    const { container } = renderEditor()

    await userEvent.click(screen.getByRole('button', { name: /expand/i }))
    await userEvent.click(screen.getAllByRole('button', { name: /collapse/i })[0])

    expect(container.querySelector('.content-editor--expanded')).toBeNull()
  })

  test('escape collapses it too', async () => {
    // It covers the page; needing to find a button to get out would be
    // a poor trade.
    const { container } = renderEditor()

    await userEvent.click(screen.getByRole('button', { name: /expand/i }))
    await userEvent.keyboard('{Escape}')

    expect(container.querySelector('.content-editor--expanded')).toBeNull()
  })

  test('the drop zone stands aside when expanded', async () => {
    const { container } = renderEditor()
    expect(container.querySelector('.content-editor-drop')).not.toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /expand/i }))

    expect(container.querySelector('.content-editor-drop')).toBeNull()
  })

  test('a read-only entry has no drop zone but still expands', async () => {
    const { container } = renderEditor({ readOnly: true })

    expect(container.querySelector('.content-editor-drop')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /expand/i }))
    expect(container.querySelector('.content-editor--expanded')).not.toBeNull()
  })

  test('an extra action sits alongside, not instead of, Expand', () => {
    renderEditor({ action: <button type="button">Load template</button> })

    expect(screen.getByRole('button', { name: /load template/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /expand/i })).toBeInTheDocument()
  })
})
