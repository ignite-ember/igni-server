import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Banner, Button, FormControl, Stack, Text, Textarea, TextInput } from '@primer/react'
import { DownloadIcon } from '@primer/octicons-react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { SubjectAccessReport } from '../../types'

/** Everything the deployment holds about one address, as one downloadable document. */
export function SubjectAccessTab() {
  const [email, setEmail] = useState('')

  const request = useMutation({
    mutationFn: (address: string) =>
      client
        .post<SubjectAccessReport>('/portal/subject-access', { email: address })
        .then((r) => r.data),
  })

  return (
    <Stack gap="normal">
      <Text size="small" className="compliance-hint">
        Everything this deployment holds about one address, with the context GDPR
        Article 15 also requires — purposes, categories, recipients, retention, source.
        Credentials are withheld and the report says which. Other contributors&apos;
        commits are excluded: answering one person&apos;s request must not disclose
        another&apos;s data.
      </Text>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (email.trim()) request.mutate(email.trim())
        }}
      >
        <Stack direction="horizontal" gap="normal" align="end">
          <FormControl>
            <FormControl.Label>Email address</FormControl.Label>
            <TextInput
              type="email"
              value={email}
              placeholder="person@example.com"
              onChange={(e) => setEmail(e.target.value)}
            />
            <FormControl.Caption>
              The subject may have no account here; the report is keyed on the address.
            </FormControl.Caption>
          </FormControl>
          <Button type="submit" variant="primary" disabled={request.isPending || !email.trim()}>
            {request.isPending ? 'Collecting…' : 'Produce report'}
          </Button>
        </Stack>
      </form>

      {request.isError && (
        <Banner
          variant="critical"
          title="Could not produce the report"
          description={refusalOf(request.error)}
        />
      )}

      {request.data && <SubjectAccessDocument report={request.data} email={email.trim()} />}
    </Stack>
  )
}

/**
 * The whole report, verbatim. A subject access response is a legal
 * artefact whose shape follows the schema, so it is shown and downloaded
 * in full rather than summarised.
 */
function SubjectAccessDocument({ report, email }: { report: SubjectAccessReport; email: string }) {
  const text = JSON.stringify(report, null, 2)

  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `subject-access-${email}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Stack gap="condensed">
      <Stack direction="horizontal" gap="condensed" align="center">
        <Text weight="semibold">Report</Text>
        <Button size="small" leadingVisual={DownloadIcon} onClick={download}>
          Download
        </Button>
      </Stack>
      <Textarea
        readOnly
        rows={24}
        className="compliance-report"
        value={text}
        aria-label="Subject access report"
      />
    </Stack>
  )
}
