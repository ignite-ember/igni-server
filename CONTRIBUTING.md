# Contributing to igni server

Thank you for helping. igni server is maintained by Ignite Ember and is
Apache-2.0. Bug reports, documentation fixes and pull requests are all
welcome.

**Security problems are not issues.** Please report them privately, as
[SECURITY.md](SECURITY.md) describes, and not in a public issue or pull
request.

## Before you start

For anything larger than a small fix, open an issue first and say what you
want to change and why. It saves you writing something we cannot take,
for example because it would make the server call out of the customer's
network (`tests/test_nothing_phones_home.py` fails the build if it does).

## Setting up

You need Python 3.13, [uv](https://docs.astral.sh/uv/), PostgreSQL (with a
role that has `CREATEDB`) and Redis. Node 20 if you work on the portal.

```bash
git clone https://github.com/ignite-ember/igni-server
cd igni-server
uv sync --group dev
cp dist.env .env      # set SECRET_ENCRYPTION_KEY at least; see the README
make migrate
make run              # the API on 127.0.0.1:7777
```

Or run the whole stack in containers with `make up` (see the README's
Docker Compose section).

## Checks

Run these before you open a pull request. CI runs the same ones.

```bash
make lint             # black --check, ruff, mypy
make test             # the Python suite: needs Postgres on 5432 and Redis on 6379
make check-docs       # the generated compliance documents are current
cd portal && npx tsc --noEmit -p . && npx vitest run
```

If you change `docs/COMPLIANCE_TRACKER.md`, run `make docs` and commit the
regenerated documents with it.

[CODE_STANDARDS.md](CODE_STANDARDS.md) describes how the code here is
written, and [CODE_GRADES.md](CODE_GRADES.md) how it is reviewed. A change
that fixes a defect should come with the test that fails without it.

## Pull requests

- Keep one change per pull request, and say in the description what it
  changes and how you checked it.
- Add or update tests, and update the docs a reader would otherwise find
  wrong.
- Every commit must be signed off (see below).

## Developer Certificate of Origin

We do not ask for a contributor licence agreement. Instead, every commit
must carry a `Signed-off-by` line, which certifies that you wrote the
change or otherwise have the right to submit it under the project's
licence, as set out in the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):

```
Signed-off-by: Your Name <you@example.com>
```

`git commit -s` adds it for you, using your `user.name` and
`user.email`. To sign off commits you have already made on a branch:

```bash
git rebase --signoff main
```

Contributions are accepted under Apache-2.0, the licence of this
repository (its section 5, "inbound = outbound"). A pull request whose
commits are not all signed off cannot be merged; the DCO check on the
pull request says which ones are missing.

## Conduct

Be respectful and assume good faith. We may remove comments, and block
people, that are abusive or harassing.
