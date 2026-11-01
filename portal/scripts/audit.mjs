// `npm audit --omit=dev`, failing on every advisory but the ones below.
//
// npm audit has no per-advisory ignore, and `--audit-level` would wave
// through a whole severity. Each exception here names one advisory, the
// reason, and has no fix to take; when one ships, delete the entry.
import { execFileSync } from 'node:child_process'

const ALLOWED = {
  // deepmerge prototype poisoning: no fixed release. Its only caller is
  // @primer/react's ThemeProvider, merging Primer's own theme objects with
  // one of their colour schemes; nothing from a request reaches it.
  'https://github.com/advisories/GHSA-gcqx-w48m-w2h4': 'deepmerge via @primer/react ThemeProvider',
}

let report
try {
  report = execFileSync('npm', ['audit', '--omit=dev', '--json'], { encoding: 'utf8' })
} catch (error) {
  report = error.stdout // npm exits non-zero whenever it finds anything
}
const advisories = Object.values(JSON.parse(report).vulnerabilities ?? {})
  .flatMap((v) => v.via.filter((via) => typeof via === 'object').map((via) => ({ name: v.name, ...via })))
const failing = advisories.filter((a) => !(a.url in ALLOWED))
for (const a of advisories) {
  console.log(`${a.url in ALLOWED ? 'allowed' : 'FAILS  '} ${a.severity.padEnd(8)} ${a.name}  ${a.url}`)
}
if (failing.length) process.exit(1)
console.log(`no advisories outside the ${Object.keys(ALLOWED).length} named exception(s)`)
