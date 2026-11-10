import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from 'react'
import { useDropzone } from 'react-dropzone'
import { Button, FormControl, Stack, Text } from '@primer/react'
import { ScreenFullIcon, ScreenNormalIcon, UploadIcon } from '@primer/octicons-react'

import { CONTENT_EXTENSION, EDITOR_LANGUAGE } from '../lib/entryKinds'
import type { ContentType } from '../types'
import './ContentEditor.css'

// Monaco is bundled rather than fetched from a CDN (see lib/monaco.ts),
// which makes it the largest thing the portal ships. Loaded here, on the
// pages that edit an entry, rather than in the chunk every page needs.
const Editor = lazy(() => import('./MonacoEditor'))

/**
 * The entry body, in Monaco, with room to work in.
 *
 * An agent prompt runs to hundreds of lines and a workflow is a program;
 * both were being written through a 380px window. Expanding takes the
 * editor to the whole viewport and back, and Escape collapses it —
 * anything that covers the page needs a way out that does not require
 * finding a button.
 *
 * Shared because the new-entry and edit-entry pages had the same Monaco
 * block, the same drop zone and the same options object, in two copies.
 */
export function ContentEditor({
  value,
  onChange,
  contentType,
  readOnly = false,
  error,
  action,
}: {
  value: string
  onChange: (next: string) => void
  contentType: ContentType
  readOnly?: boolean
  error?: string | null
  /** An extra control for the label row, such as "Load template". */
  action?: ReactNode
}) {
  const [expanded, setExpanded] = useState(false)

  const onDrop = useCallback(
    (files: File[]) => {
      if (files.length === 0) return
      const reader = new FileReader()
      reader.onload = () => {
        if (typeof reader.result === 'string') onChange(reader.result)
      }
      reader.readAsText(files[0])
    },
    [onChange],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop, multiple: false })

  useEffect(() => {
    if (!expanded) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [expanded])

  return (
    <FormControl>
      <FormControl.Label>
        <Stack direction="horizontal" justify="space-between" align="center">
          <span>Content</span>
          <Stack direction="horizontal" gap="condensed" align="center">
            <Text size="small" className="group-detail-muted">
              {readOnly ? 'Read-only.' : 'Drop a file to replace it, or edit below.'}
            </Text>
            {action}
            <Button
              size="small"
              type="button"
              onClick={() => setExpanded((v) => !v)}
              leadingVisual={expanded ? ScreenNormalIcon : ScreenFullIcon}
            >
              {expanded ? 'Collapse' : 'Expand'}
            </Button>
          </Stack>
        </Stack>
      </FormControl.Label>

      <div className={expanded ? 'content-editor content-editor--expanded' : 'content-editor'}>
        {expanded && (
          <Stack
            direction="horizontal"
            justify="space-between"
            align="center"
            className="content-editor-bar"
          >
            <Text size="small" weight="semibold">
              Content
            </Text>
            <Button
              size="small"
              type="button"
              onClick={() => setExpanded(false)}
              leadingVisual={ScreenNormalIcon}
            >
              Collapse
            </Button>
          </Stack>
        )}

        {!readOnly && !expanded && (
          <div
            {...getRootProps()}
            className={`content-editor-drop${isDragActive ? ' content-editor-drop--active' : ''}`}
          >
            <input {...getInputProps()} />
            <UploadIcon size={20} className="group-detail-muted" />
            <Text size="small" className="group-detail-muted">
              Click or drop a {CONTENT_EXTENSION[contentType]} file here
            </Text>
          </div>
        )}

        <div className="content-editor-frame">
          <Suspense fallback={<div className="content-editor-loading">Loading editor…</div>}>
            <Editor
              height="100%"
              language={EDITOR_LANGUAGE[contentType]}
              value={value}
              onChange={(v) => onChange(v ?? '')}
              theme="github-light"
              options={{
                readOnly,
                minimap: { enabled: expanded },
                fontSize: 13,
                wordWrap: 'on',
                scrollBeyondLastLine: false,
                // Monaco does not observe its parent's size; without this
                // it collapses to a couple of pixels when the container
                // changes, which is exactly what expanding does.
                automaticLayout: true,
              }}
            />
          </Suspense>
        </div>
      </div>

      {error && <FormControl.Validation variant="error">{error}</FormControl.Validation>}
    </FormControl>
  )
}
