/**
 * The Monaco editor component, with Monaco bundled.
 *
 * Its own module so `ContentEditor` can load it lazily: Monaco is most
 * of a megabyte, and only the entry pages need it. Importing
 * `../lib/monaco` first is the point — it hands the loader the bundled
 * Monaco before any editor mounts, so nothing is fetched from a CDN.
 */
import '../lib/monaco'
import Editor from '@monaco-editor/react'

export default Editor
