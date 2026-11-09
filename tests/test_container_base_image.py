"""The shipped image's base and its security updates, asserted from the file.

The scheduled vulnerability scan (V-4, V-7) reads the image that CI
builds from `containers/be/Dockerfile` and gates on its HIGH and CRITICAL
findings. On 2026-11-09 it went red on two HIGH advisories in the base
OS's zlib — CVE-2026-76844 and CVE-2026-85091, a heap buffer overflow in
``gz_vacate()`` — and the scan's own table said what it wanted:

    zlib1g  CVE-2026-76844  HIGH  fixed
            1:1.3.dfsg+really1.3.1-1+b1 → 1:1.3.dfsg+really1.3.1-1+deb13u1

Two things about that are worth stating, because either one alone leads
to the wrong fix:

* **The tag was not the problem.** `python:3.13-slim` had already become
  trixie — the scan reports the image as ``debian 13.7`` — so renaming
  the tag to ``python:3.13-slim-trixie`` changes the declaration and
  nothing else. The two tags are the same image, digest for digest.
* **The base image was the problem.** The fixed version exists, in
  trixie-security, but the published image was built before that update
  landed and still carries ``+b1``. So the update has to be installed in
  the image.

So this file asserts two separate things: that the base image is *named*
rather than inherited, and that the security update is *installed*.

The second is the one that matters, and the reason it is asserted here
rather than left to the daily scan: a scan reports a finding, it does
not prevent the next build from going back to the vulnerable version.
That is what happened the first time — the fix belongs in the Dockerfile
or it does not exist.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DOCKERFILE = _ROOT / 'containers/be/Dockerfile'


def _stages() -> list[tuple[str, str | None, str]]:
    """The Dockerfile split at its `FROM` lines.

    A stage is what a `FROM` starts, and everything up to the next one
    belongs to it. Each stage is (image, alias, body): the alias is
    ``builder`` for the first stage and absent for the last, which is
    the image that ships.

    A list, not a mapping: the two stages name the same base image, and
    a mapping keyed by that name would silently keep one of them.
    """
    text = _DOCKERFILE.read_text()
    matches = list(re.finditer(r'^FROM\s+(\S+)(?:\s+AS\s+(\S+))?\s*$', text, flags=re.M))
    assert matches, 'no FROM line found in the Dockerfile'
    stages = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        stages.append((match.group(1), match.group(2), text[match.end() : end]))
    return stages


def _code_lines(text: str) -> str:
    """The Dockerfile's commands, with its comments removed.

    This file's comments quote the commands they explain — including
    the one they explain *not* to run — so a search for a command has
    to look at the code and not at the prose about it.
    """
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))


def _upgrade_command(body: str) -> re.Match[str] | None:
    """The `--only-upgrade` command itself, not the prose about it.

    This Dockerfile's comment quotes the flag in backticks — "``--only-
    upgrade`` on purpose" — so a plain search finds the prose first and
    every ordering assertion made against it would be made against the
    wrong text.

    The arguments are captured, not merely searched for: what follows the
    flag is what decides whether the update is the one package the scan
    named or the whole image.
    """
    return re.search(r'--only-upgrade\s+(?!`)([^\\\n&]+)', body)


def _upgrades_zlib1g(body: str) -> bool:
    """Whether this stage installs the zlib1g security update.

    The package name and nothing else, and nothing else *besides* it
    either: `zlib1g-dev` is the headers rather than the library, and
    `--only-upgrade zlib1g` matches as a prefix of it. So the argument
    list is read whole and compared, rather than searched for.
    """
    match = _upgrade_command(body)
    if not match:
        return False
    return match.group(1).split() == ['zlib1g']


class TestTheBaseImageIsNamed:
    """`python:3.13-slim` is a moving tag.

    It was bookworm and became trixie, silently, between two builds of
    this image. The Dockerfile did not change by a line; the OS under it
    moved, and the zlib in the shipped image changed with it. Writing the
    release into the tag makes the base an explicit, auditable choice
    instead of a property of whatever the tag pointed at that morning.
    """

    def test_no_stage_uses_the_bare_moving_tag(self):
        """`python:3.13-slim` exactly: the tag whose target moved."""
        bare = re.findall(r'^FROM\s+python:3\.13-slim(\s|$)', _DOCKERFILE.read_text(), flags=re.M)
        assert not bare, (
            'the bare `python:3.13-slim` tag is the one that moved from bookworm to '
            'trixie without this file changing; name the Debian release instead'
        )

    def test_every_stage_names_a_debian_release(self):
        for image, _alias, _body in _stages():
            if not image.startswith('python:'):
                continue
            assert re.search(r'-(bookworm|trixie|forky|sid)\b', image), (
                f'{image} does not name its Debian release'
            )

    def test_both_stages_use_the_same_base(self):
        """The builder resolves and freezes the dependencies and stage 2
        installs them. If the two stages disagreed about the base, the
        wheels could be built against a glibc the runtime does not have."""
        images = [image for image, _alias, _body in _stages() if image.startswith('python:')]
        assert len(images) >= 2, images
        assert len(set(images)) == 1, f'the stages disagree about the base image: {images}'

    def test_the_release_is_trixie(self):
        """Deliberately a specific release, so that moving to the next
        one is a decision somebody makes and writes down rather than
        something that happens between two builds.

        It is trixie because trixie-security is where the zlib fix for
        CVE-2026-76844 and CVE-2026-85091 was published.
        """
        images = [image for image, _alias, _body in _stages() if image.startswith('python:')]
        assert all('-trixie' in image for image in images), images


class TestTheSecurityUpdateIsInstalled:
    """The finding the scan reported, closed in the file that builds the
    image it scanned."""

    def test_the_final_stage_upgrades_zlib1g(self):
        """`--only-upgrade`, by name.

        The scan reported the *installed* version, so the fix is an
        installed version. `--only-upgrade zlib1g` takes the security
        update and leaves every other package in the image exactly where
        the base image put it.
        """
        final = _stages()[-1][2]
        assert _upgrades_zlib1g(final), (
            'the final stage does not install the zlib1g security update; '
            'the base image still ships the version the scan flagged'
        )

    def test_the_upgrade_is_in_the_final_stage_and_not_only_the_builder(self):
        """The builder's copy of zlib is not shipped, so upgrading it
        alone would leave the published image vulnerable while the build
        looked fixed."""
        stages = _stages()
        builder = stages[0][2]
        final = stages[-1][2]
        assert _upgrades_zlib1g(final)
        assert not _upgrades_zlib1g(builder), (
            'the upgrade is in the builder stage, which is not the image that ships'
        )

    def test_it_is_not_a_full_upgrade(self):
        """A full `apt-get upgrade` would drag every package in the base
        image forward: a different change, with a different blast radius,
        that nobody asked for and cannot review from a diff of one line.

        Read from the commands and not from the prose, because this
        file's comments quote the command they explain *not* to run.
        """
        code = _code_lines(_DOCKERFILE.read_text())
        assert not re.search(r'apt-get\s+(?:-[^\s]+\s+)*upgrade\b', code), (
            'the image upgrades every package; the fix should be the one package '
            'the scan named'
        )

    def test_the_package_lists_are_removed_afterwards(self):
        """An index left in the image is an index that is wrong the day
        after the build, and it is several megabytes of it."""
        final = _stages()[-1][2]
        upgrade = _upgrade_command(final)
        assert upgrade, 'the final stage does not install the security update'
        cleanup = final.find('rm -rf /var/lib/apt/lists/*', upgrade.end())
        assert cleanup != -1, 'the apt lists are left in the image'
        assert cleanup > upgrade.end(), 'the apt lists are removed before the upgrade'

    def test_the_two_advisories_are_named_in_the_file(self):
        """The change says which advisories it closes and why, so that a
        reader — or a scanner — can tell a deliberate fix from an
        accident."""
        text = _DOCKERFILE.read_text()
        assert 'CVE-2026-76844' in text
        assert 'CVE-2026-85091' in text

    def test_the_fixed_version_is_the_one_the_scan_asked_for(self):
        """The scan named the version it wanted. The image is built by
        installing the security update rather than by pinning a string,
        so this asserts the file records the same version the scan
        named — the two cannot drift apart unnoticed."""
        text = _DOCKERFILE.read_text()
        assert '1:1.3.dfsg+really1.3.1-1+deb13u1' in text
