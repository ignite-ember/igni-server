import { Link as RouterLink } from 'react-router-dom'
import { Banner, Button, Stack } from '@primer/react'

/**
 * In place of the new-entry form for the group that holds what igni ships.
 *
 * It takes no additions. The portal does not link to the form for it, but
 * the URL is guessable and the server would refuse the POST — better to
 * say so than to let somebody write an agent and lose it on submit.
 */
export function ShippedGroupNotice({ groupId }: { groupId: string | undefined }) {
  return (
    <Stack gap="spacious">
      <Banner
        variant="info"
        title="This is what igni ships"
        description="Nothing can be added to it. Create a group from it, and add to that instead."
      />
      <Stack direction="horizontal" gap="condensed">
        <Button as={RouterLink} to={`/groups/${groupId}?tab=entries`} variant="primary">
          Back to the group
        </Button>
        <Button as={RouterLink} to="/groups/new">
          New group from it
        </Button>
      </Stack>
    </Stack>
  )
}
