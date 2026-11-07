"""One JSON object per line, for whatever the customer ships logs to.

F102. The default format is for a person reading `docker logs`:

    2026-09-02 14:31:02.881 | WARNING:app.security.startup_checks:No SMTP…

which is unambiguous to a human and, to Loki or OpenSearch or CloudWatch,
one long string. Everything a customer might want to filter on — the
level, the module, which repository a task was working on — is inside
that string, so the answer to "show me every error from the webhook
service for repo X last Tuesday" is a regular expression somebody has to
write and maintain.

`LOG_FORMAT=json` emits the same records as objects instead. Nothing is
sent anywhere; this only changes the shape of what a shipper picks up.

**The field list is explicit, and that is the security property.** The
obvious implementation dumps `record.__dict__`, which carries whatever
any caller happened to attach via `extra=` — a category that includes
tokens somebody logged in a hurry, and that grows every time anybody adds
a log line. So the formatter names its fields, and a test asserts an
unexpected `extra` does not appear in the output. LG-5 already had to
remove commit messages and author emails from accepted-webhook logs; this
is the same problem at the level of the format rather than the call site.

**Timestamps are UTC, with milliseconds, in ISO 8601 with a `Z`.** The
text format learned this the hard way — it printed a literal `.%f` for
every line of its first production boot, because `datefmt` goes to
`time.strftime`, which has no `%f`.
"""

from __future__ import annotations

import datetime as _datetime
import json
import logging

# Everything the formatter will emit, and nothing else. A caller's
# `extra=` cannot introduce a field here.
_FIELDS = ('ts', 'level', 'logger', 'message', 'exception')


class JsonFormatter(logging.Formatter):
    """A single JSON object per record, with a fixed set of keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            'ts': _datetime.datetime.fromtimestamp(record.created, tz=_datetime.timezone.utc)
            .isoformat(timespec='milliseconds')
            .replace('+00:00', 'Z'),
            'level': record.levelname,
            'logger': record.name,
            # `getMessage()` rather than `record.msg`: the arguments have
            # to be interpolated here, or every parameterised log line
            # arrives as a format string with the values in a separate
            # array nobody joins back up.
            'message': record.getMessage(),
        }

        if record.exc_info:
            # As one string rather than a nested structure. A traceback is
            # read by a person, and every log system in use can display a
            # multi-line string field; few can usefully index a list of
            # frames.
            payload['exception'] = self.formatException(record.exc_info)

        # `ensure_ascii=False`: the messages contain non-ASCII, and an
        # escaped \\ud83d in a log viewer is a worse line than the emoji.
        return json.dumps(
            {key: value for key, value in payload.items() if value not in ('', None)},
            ensure_ascii=False,
        )


def configure(level: str, *, as_json: bool) -> None:
    """Install the root handler.

    `force=True` because uvicorn configures logging on the
    way in, and the last caller wins — this being called from `app.main`
    at import puts it after uvicorn's and before any of ours.
    """
    handler = logging.StreamHandler()

    if as_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt='%(asctime)s.%(msecs)03d | %(levelname)s:%(name)s:%(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            )
        )

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        handlers=[handler],
        force=True,
    )
