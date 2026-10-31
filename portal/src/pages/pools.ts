/**
 * What a node's pools are called, and the order they are shown in.
 *
 * F132. These lived in ``InferencePage.tsx`` and the labels were
 * rendered straight from ``node.pools`` in four places — whatever order
 * the API sent — so the list showed one row reading "CodeIndex · Chat"
 * above two reading "Chat · CodeIndex". Nothing was wrong with any
 * single row; the page just looked unmaintained.
 *
 * Its own module because a component file that also exports helpers
 * loses fast refresh, and because the order below is vocabulary the
 * page borrows rather than something it owns.
 */

export const POOL_TITLES: Record<string, string> = {
  chat: "Chat",
  codeindex: "CodeIndex",
};

/**
 * The pools a node serves, in a fixed order.
 *
 * F132. These were rendered straight from ``node.pools``, which is
 * whatever order the API sent — so one row read "CodeIndex · Chat"
 * while the two beneath it read "Chat · CodeIndex". The order comes
 * from ``POOL_TITLES`` above, so adding a pool decides its position
 * once, in the place its name is already declared.
 */
const POOL_ORDER = Object.keys(POOL_TITLES);

export function poolLabels(pools: readonly string[]): string[] {
  return [...pools]
    .sort((a, b) => {
      const ia = POOL_ORDER.indexOf(a);
      const ib = POOL_ORDER.indexOf(b);
      // An unknown pool sorts last rather than first: it is a name we
      // do not have a title for, and it should not lead the list.
      return (ia === -1 ? POOL_ORDER.length : ia) - (ib === -1 ? POOL_ORDER.length : ib);
    })
    .map((pool) => POOL_TITLES[pool] ?? pool);
}

