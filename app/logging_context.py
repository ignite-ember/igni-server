"""Request-scoped logging context using contextvars.

Adds `repo`, `event_id` and `commit` to every record the JSON formatter
emits, with no change to any `logger.xxx()` call.

**Nothing writes to it today**, and that is worth stating rather than
leaving to be discovered. It was built for the Celery task that indexed a
push, and DEC-13 removed the worker — so `set_task_context` has no
callers. What remains is live rather than dead: `TaskContextFilter` is
installed on the handler and `task_context_fields()` is called for every
record, so the three keys are part of the formatter's fixed field list
(LG-5) and are asserted by `test_you_can_see_everything_yourself.py`.

Kept because the fields are the right ones for this product — a log line
about a push is far more useful with the repository and the commit on it
— and because removing them means editing the formatter's asserted
surface, which is a separate change from removing a worker. Whoever adds
webhook context next should call `set_task_context` at the entry point
rather than inventing a second mechanism.
"""

from contextvars import ContextVar
import logging

_repo_name: ContextVar[str] = ContextVar('_repo_name', default='')
_event_id: ContextVar[str] = ContextVar('_event_id', default='')
_commit_sha: ContextVar[str] = ContextVar('_commit_sha', default='')


def set_task_context(
    repo_name: str = '',
    event_id: int | str = '',
    commit_sha: str = '',
) -> None:
    """Set the logging context for the current task or request.

    Call once at an entry point. Had one caller — the indexing task
    DEC-13 removed — and none now; see the module docstring.
    """
    _repo_name.set(str(repo_name))
    _event_id.set(str(event_id))
    _commit_sha.set(str(commit_sha))


def update_commit_sha(sha: str) -> None:
    """Update the commit SHA in the current context (call per-commit in a push loop)."""
    _commit_sha.set(sha)


def clear_task_context() -> None:
    """Clear all task-scoped context (call at task exit)."""
    _repo_name.set('')
    _event_id.set('')
    _commit_sha.set('')


def task_context_fields() -> dict[str, str]:
    """The same context the prefix renders, as separate fields.

    Used by the JSON formatter. `[repo|evt:12|abc1234]` is compact for a
    person and opaque to a query; `repo`, `event_id` and `commit` are
    filterable, which is the whole reason for the JSON format.

    Empty values are omitted rather than written as "" — a log system
    that indexes a field which is blank on 99% of lines pays for the
    index and answers no question.
    """
    fields = {'repo': _repo_name.get(), 'event_id': _event_id.get(), 'commit': _commit_sha.get()[:7]}
    return {key: value for key, value in fields.items() if value}


class TaskContextFilter(logging.Filter):
    """Logging filter that injects a task_prefix attribute into every LogRecord.

    When context vars are set, produces:
        [repo_name|evt:ID|sha7]

    When no context is set — which is every record today, see the module
    docstring — produces an empty string, so log messages are
    unaffected.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        repo = _repo_name.get()
        event = _event_id.get()
        sha = _commit_sha.get()

        if repo or event or sha:
            parts = []
            if repo:
                parts.append(repo)
            if event:
                parts.append(f'evt:{event}')
            if sha:
                parts.append(sha[:7])
            record.task_prefix = f'[{"|".join(parts)}] '  # type: ignore[attr-defined]
        else:
            record.task_prefix = ''  # type: ignore[attr-defined]

        return True
