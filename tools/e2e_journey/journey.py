"""The free base's cross-repo journey, against a running stack.

Run through ``journey.sh run`` (which brings the stack up first), from
the repository root with the server's Python::

    python -m tools.e2e_journey.journey --api http://localhost:17801 --portal http://localhost:5201 ...

Every step records PASS / FAIL / SKIP / N/A with the calls it made and
the answers, to ``$out/results.json`` and ``$out/results.md``; browser
screenshots go to ``$out/shots``. A failed step does not stop the run.
Exits 1 when any step failed.
"""

import argparse
import sys

from tools.e2e_journey import steps_cli, steps_governance, steps_identity, steps_offboarding, steps_service
from tools.e2e_journey.harness import Journey, Options

STEPS = steps_identity.STEPS + steps_cli.STEPS + steps_governance.STEPS + steps_service.STEPS + steps_offboarding.STEPS


def options() -> Options:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for field, info in Options.model_fields.items():
        flag = '--' + field.replace('_', '-')
        if info.is_required():
            parser.add_argument(flag, dest=field, required=True)
        else:
            parser.add_argument(flag, dest=field, default=info.default)
    return Options(**vars(parser.parse_args()))


def main() -> None:
    journey = Journey(options())
    journey.run(STEPS)
    sys.exit(0 if journey.write_report() else 1)


if __name__ == '__main__':
    main()
