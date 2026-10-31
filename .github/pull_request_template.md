## What this changes

## How I checked it

- [ ] `make lint` and `make test` pass
- [ ] Portal changes: `npx tsc --noEmit -p . && npx vitest run` in `portal/`
- [ ] `make check-docs` passes (if `docs/COMPLIANCE_TRACKER.md` or a generator changed)
- [ ] Every commit is signed off (`git commit -s`; see CONTRIBUTING.md)

Security problems: please report them privately (SECURITY.md), not in a pull request.
