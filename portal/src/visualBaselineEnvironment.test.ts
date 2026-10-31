/**
 * The visual baselines are only comparable in the environment they
 * were generated in, so that environment is pinned in three places.
 * This is the test that keeps the three equal.
 *
 * E4/E5. The portal's numbers happened to agree, twice — the range in
 * package.json equalled the lockfile, and its baselines rendered the
 * same on arm64 and x86_64. The desktop client's did neither: a
 * `^1.61.0` range against a `1.61.1` lockfile failed the whole suite
 * with "Please update docker image as well", and baselines generated
 * on an arm64 Mac failed all 34 under x86_64. Both repositories pin
 * all three now, and this is the test that keeps them equal.
 *
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const CLIENT = join(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = join(CLIENT, "..");

function lockfilePlaywrightVersion(): string {
  const lock = JSON.parse(
    readFileSync(join(CLIENT, "package-lock.json"), "utf8"),
  ) as { packages: Record<string, { version: string }> };
  const entry = lock.packages["node_modules/@playwright/test"];
  expect(entry, "@playwright/test is not in the lockfile").toBeTruthy();
  return entry.version;
}

describe("the visual baselines' environment is pinned", () => {
  const version = lockfilePlaywrightVersion();
  const workflow = readFileSync(join(REPO, ".github", "workflows", "ci.yml"), "utf8");
  const script = readFileSync(join(CLIENT, "scripts", "baselines-linux.sh"), "utf8");

  it("reads a plausible version from the lockfile", () => {
    // A test that derives an empty string agrees with everything.
    expect(version).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("CI's container is the lockfile's Playwright version", () => {
    expect(
      workflow,
      `ci.yml must run the portal job in ` +
        `mcr.microsoft.com/playwright:v${version}-noble — the version ` +
        `npm ci installs. A mismatch fails every test with "Please ` +
        `update docker image as well".`,
    ).toContain(`container: mcr.microsoft.com/playwright:v${version}-noble`);
  });

  it("the baseline script derives its image from the lockfile", () => {
    // Not from package.json: that is the range, not what is installed.
    expect(script).toContain("package-lock.json");
    expect(script).not.toMatch(/require\([^)]*package\.json'\)\.devDependencies/);
  });

  it("the baseline script pins the architecture CI runs on", () => {
    expect(
      script,
      "baselines generated on a developer's arm64 Mac failed all 34 " +
        "under x86_64 — pin --platform so the environment is stated",
    ).toContain("--platform linux/amd64");
  });

  it("CI does not install a second copy of the browsers", () => {
    // The image ships browsers matching its own Playwright. A
    // `playwright install` on top is how the renderer stops matching
    // what the baselines were taken with.
    // The job block only: from its key to the next sibling key. The
    // first version of this sliced on the next two-space-indented
    // line, which is every step in the job — so it read the wrong text
    // and failed on a workflow that was correct.
    const after = workflow.slice(workflow.indexOf("  lint-portal:"));
    const nextKey = /\n {2}[a-z][\w-]*:\n/.exec(after.slice(1));
    const job = nextKey ? after.slice(0, nextKey.index + 1) : after;
    expect(job).toContain("container: mcr.microsoft.com/playwright");
    // Comments stripped: the only "playwright install" in this job is
    // the comment above saying there is not one, and the rule read its
    // own explanation as the thing it forbids — F126's shape, arriving
    // as a false positive instead of a false pass.
    const steps = job
      .split("\n")
      .filter((l) => !l.trim().startsWith("#"))
      .join("\n");
    expect(steps).not.toContain("playwright install");
  });
});
