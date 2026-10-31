/**
 * A reporter that will not let this suite report green while quietly
 * not running things.
 *
 * The same rule as the desktop client's suite, for the same reason
 * (F128): a run that skips has to say so, in words, every time. The
 * rule is not a list of tests allowed to skip — a list goes stale on
 * the next spec — it is that **every skip must be declared by a tag on
 * the test**. A tagged skip is a limitation someone chose and wrote
 * down. An untagged one is an accident, and under
 * ``IGNI_E2E_STRICT=1`` it fails the run.
 *
 * Strict mode belongs in CI. Locally the run still passes, but it says
 * what it did not check.
 */

import type {
  FullConfig,
  FullResult,
  Reporter,
  TestCase,
  TestResult,
} from '@playwright/test/reporter'

/** Tags that declare a skip, and what each one means. */
const DECLARED: Record<string, string> = {
  '@manual':
    'a by-hand check, not something CI can assert — a screenshot ' +
    'generator or a flow that needs seeded data',
  '@needs-baseline':
    'a pixel comparison with no committed baseline for this platform; ' +
    'Playwright names baselines per-OS and only darwin ones are in the ' +
    'repository. Commit one for this platform and it runs',
}

type Skip = {
  title: string
  file: string
  line: number
  reason: string
  declaredBy: string | null
}

const BAR = '─'.repeat(72)

export default class SkipReporter implements Reporter {
  private skips: Skip[] = []
  private ran = 0
  private strict = process.env.IGNI_E2E_STRICT === '1'
  private rootDir = ''

  onBegin(config: FullConfig): void {
    this.rootDir = config.rootDir
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status !== 'skipped') {
      this.ran += 1
      return
    }
    const declaredBy = test.tags.find((t) => t in DECLARED) ?? null
    this.skips.push({
      title: test.titlePath().slice(3).join(' › ') || test.title,
      file: test.location.file.replace(`${this.rootDir}/`, ''),
      line: test.location.line,
      reason:
        result.annotations?.find((a) => a.type === 'skip')?.description ??
        test.annotations.find((a) => a.type === 'skip')?.description ??
        'no reason given',
      declaredBy,
    })
  }

  async onEnd(result: FullResult): Promise<{ status?: FullResult['status'] }> {
    if (this.skips.length === 0) {
      console.log(`\n${BAR}\nEvery test ran. Nothing was skipped.\n${BAR}`)
      return {}
    }

    const undeclared = this.skips.filter((s) => !s.declaredBy)
    const declared = this.skips.filter((s) => s.declaredBy)

    const out: string[] = ['', BAR]
    out.push(
      `${this.ran} tests ran. ${this.skips.length} did NOT — this run does ` +
        `not prove what they cover.`,
    )

    if (declared.length) {
      out.push('', `Declared (${declared.length}) — deliberate, and why:`)
      for (const s of declared) {
        out.push(`  · ${s.title}`)
        out.push(`      ${s.file}:${s.line}  ${s.declaredBy}`)
        out.push(`      ${DECLARED[s.declaredBy!]}`)
        out.push(`      reason given: ${s.reason}`)
      }
    }

    if (undeclared.length) {
      out.push(
        '',
        `Undeclared (${undeclared.length}) — no tag says these are meant ` +
          `to skip:`,
      )
      for (const s of undeclared) {
        out.push(`  ✗ ${s.title}`)
        out.push(`      ${s.file}:${s.line}`)
        out.push(`      reason given: ${s.reason}`)
      }
      out.push(
        '',
        '  Either make them runnable against the stack the suite already',
        '  needs — the backend is checked before the run starts, see',
        '  tests/e2e/global-setup.ts — or tag them with one of:',
        `    ${Object.keys(DECLARED).join('  ')}`,
      )
    }

    out.push(BAR, '')
    console.log(out.join('\n'))

    if (this.strict && undeclared.length) {
      console.log(
        `IGNI_E2E_STRICT=1 and ${undeclared.length} test(s) skipped ` +
          `without a tag. Failing the run.\n`,
      )
      return { status: 'failed' }
    }
    if (!this.strict && undeclared.length) {
      console.log(
        'Set IGNI_E2E_STRICT=1 to make an undeclared skip fail, the way ' +
          'CI does.\n',
      )
    }
    return { status: result.status }
  }
}
