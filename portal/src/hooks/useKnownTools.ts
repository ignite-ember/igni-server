import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

/**
 * The tool names this deployment offers.
 *
 * From the server, so the live warning and the one on save check
 * against the same list rather than two copies that can disagree.
 */
export function useKnownTools(kind: string): string[] {
  const toolsQuery = useQuery({
    queryKey: ['agent-tools'],
    queryFn: () => client.get<{ tools: string[] }>('/portal/agent-tools').then((r) => r.data),
    enabled: kind === 'agents',
    staleTime: 5 * 60 * 1000,
  })
  return kind === 'agents' ? (toolsQuery.data?.tools ?? []) : []
}
