/**
 * igni brand logo — the white flame on its coral tile, matching the favicon.
 * Used on login page, header, and landing.
 */
export function IgniLogo({ size = 48 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="igni"
      role="img"
    >
      <rect width="64" height="64" rx="15" fill="#D98457" />
      <path
        fill="#fff"
        fillRule="evenodd"
        transform="translate(32 33) scale(.8) translate(-32 -32)"
        d="M35 3C31 12 13 23 13 40a19 19 0 0 0 38 0c0-8-3.5-14-8-19c-.5 5-3 8.5-7 9.5c3-8 2-19-1-27.5Z M32 33c-3 4-7.5 7.5-7.5 12.5a7.5 7.5 0 0 0 15 0c0-5-4.5-8.5-7.5-12.5Z"
      />
    </svg>
  )
}
