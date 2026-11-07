"""Answering a subject access request. GDPR Article 15.

DP-8 in `COMPLIANCE_TRACKER.md`. Before this, a customer receiving a
request had one option: write SQL across every table that holds personal
data, which is not a plan — it is a promise to miss something.

## Article 15 is not a database dump

It grants a copy of the personal data **plus** the context: the purposes
of processing, the categories of data, the recipients, the retention
period and the source. A report that returns rows and no context answers
half the article, so `ProcessingDescription` (`processing.py`) builds
the other half from what the deployment is actually configured to do.

## Two hazards, and they pull in opposite directions

**Never return a credential.** `users.hashed_password` and
`user_roles.email_verification_token` are deliberately excluded. A
password hash is not the subject's personal data in any useful sense, and
handing one to whoever persuades an administrator to run an export is a
gift to an attacker. The report says the columns exist and were withheld,
because silently omitting them would make the export look complete when it
is not. Each withheld column carries that reason on its own entry in
`personal_columns.py`, so a withheld column the report does not explain
cannot be written down.

**Never disclose a third party while complying.** Audit detail on some
routes describes somebody else — the person an erasure was run for, the
member an access review covered — so it is withheld, and the report says
why.

## Coverage is asserted, not assumed

Three rules in `tests/test_subject_access.py`, each catching what the
others cannot:

* **discovery** — any column in the live schema whose *name* references a
  person (`%user_id%`, `%email%`, `%_by%`, `%actor%`) must be classified.
  A migration adding a table with a `user_id` fails until somebody
  decides;
* **depth** — once a table appears here, **every one of its columns** must
  be classified, in `PERSONAL_DATA_COLUMNS` (`personal_columns.py`) or
  in `NOT_PERSONAL_COLUMNS` (`not_personal_columns.py`). This is the rule
  F113 asked for and the one discovery cannot do: nothing about
  `full_name` or `use_case` looks like an identifier, and the schema had
  four such columns unmapped when it was built;
* **behaviour** — every column marked `included` must actually be returned
  by, or matched on by, **each** query that reads its table. Otherwise the
  classification is a claim about the export rather than a description of
  it, which is worse than no classification: it reads as assurance.

Every column of every table judged in writing — more than anybody holds
in their head, and the schema grows.

## Where things are

* `service.py` — `SubjectAccessService`, which runs one export;
* `report_builder.py` — the report while it is assembled, and every
  sentence it says about its own completeness. The only writer of the
  sections, through `account`, `section`, `things_they_created` and
  `services_answered`;
* `processing.py` — the Article 15 context;
* `classification.py` — `ColumnRegistry`, the one reader of the two maps;
* `schemas.py` — the report, its parts, the `Section` names and its errors.

The reads themselves are `app/db/sql/subject_access.py`; what they
return — one row model per statement, and `ThingsTheyCreated` — is
`app/db/sql/schemas/subject_access.py` and `subject_access_created.py`.
"""
