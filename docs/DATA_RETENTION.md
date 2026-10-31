# Retention and deletion

For the customer's privacy and security teams. What igni server keeps, for
how long, which setting changes it, and how a person, an organisation and a
whole deployment are erased. Part of the security pack
([SECURITY_PACK.md](SECURITY_PACK.md)).

The customer is the controller and decides every retention period. igni
supplies the settings and the mechanisms. Ignite Ember holds none of the
data, so it has nothing to keep or delete.

The per-table detail, column by column, is in
[RECORDS_OF_PROCESSING.md](RECORDS_OF_PROCESSING.md). It is generated from
the code, so where this page and that one differ, that one is right.

## 1. How long each kind of data is kept

| Data | Kept | Changed by |
|---|---|---|
| Accounts | For the life of the account | Deleting the account (section 2) |
| Organisations, groups, memberships | For the life of the organisation or the membership | Removing the member, or deleting the organisation |
| Notifications | For the life of the organisation. A read notification is marked, not deleted | Nothing |
| Usage records | No automatic window. Kept for the life of the deployment unless the operator deletes them | The operator, in PostgreSQL |
| Audit trail | Everything, by default | `AUDIT_RETENTION_DAYS`: events older than this many days are deleted. 0, the default, keeps everything |
| Messages, chats, actions and events services send to a person's or an app's agent | Wait at most 14 days to be delivered, then expire | `SERVICE_EVENT_MAX_WAIT_SECONDS` |
| The same items, once delivered, expired or dropped | 30 days, so the sending service can read what became of them, then deleted | `SERVICE_EVENT_RETENTION_DAYS` (0 keeps them) |
| Files a service sends with those items | A day if no item names them; otherwise until the item expires | Fixed in the code (`app/services/cloud/service_attachments.py`) |
| A removed member's VM state, volume and keys (Fleet) | 30 days after removal by default, then erased. Paused while a legal hold stands | The organisation's `removed_member_retention_days`, within the platform's maximum (`PUT /v1/portal/orgs/{org_id}/retention`); legal holds at `/v1/portal/orgs/{org_id}/legal-holds` |
| An export of a removed member's data | 7 days, for the organisation to download | `PRODUCER_EXPORT_RETENTION_DAYS` |
| The gateway's record of a producer that is gone | 30 days after the erasure | `PRODUCER_FEED_RETENTION_DAYS` |
| A revocation telling services to stop accepting a person's tokens | 31 days, longer than any token lives | Fixed in the code |
| Backups | 14 days of runs, deleted only after a newer run is complete | `IGNI_BACKUP_RETAIN_DAYS` (compose) or `backup.retainDays` (Helm) ([BACKUP_AND_CONTINUITY.md](BACKUP_AND_CONTINUITY.md)) |
| Logs | Whatever the customer's log store keeps. igni writes to stdout and keeps no log files | The customer's log pipeline |

The deletions above are done by one retention round inside the API, once a
day (`app/services/retention.py`). It takes a PostgreSQL advisory lock, so
with several replicas only one runs it. The shipped alert rules fire when it
fails or stops running (`IgniRetentionFailing`, `IgniRetentionNotRunning`,
`IgniRetentionNeverRan` in `charts/igni/files/alerts.yaml`).

The audit trail is append-only: a database trigger refuses any `UPDATE`.
`DELETE` is allowed only so the retention round can apply
`AUDIT_RETENTION_DAYS` (LG-2 in [COMPLIANCE_TRACKER.md](COMPLIANCE_TRACKER.md)).

## 2. Erasing a person

| Step | How | What it does |
|---|---|---|
| Find what is held | `POST /v1/portal/subject-access` | Returns everything the deployment holds about an address, with the GDPR Article 15 context, and asks each approved service for its part |
| Erase their work in the services | `POST /v1/portal/erasure` | Tells every approved service (CodeIndex, Artifacts, any other) to erase or redact the person, redacting in place where other people's work shares the record. Answers 502 naming any service that did not confirm; it is safe to run again. Marks the person for erasure in Fleet, which erases their VM. Refused while a legal hold stands, and the refusal is in the audit trail |
| Delete the account | `DELETE /v1/portal/team/{user_id}` | Removes the account row. Records elsewhere keep their place with the person's id set to NULL, rather than being deleted with it. Every session the account issued stops working on the next request |

The audit trail keeps the record that an erasure happened, with the address
masked, so the trail does not become the place the erased address survives.
Earlier audit events that name the person stay until `AUDIT_RETENTION_DAYS`
removes them; the customer decides whether that is acceptable for its audit
obligations.

**Backups still hold the person until they age out.** An erasure does not
rewrite old backups. With the default retention they are gone 14 days later.
If a backup is restored, run the erasure again.

## 3. Deleting an organisation

`DELETE /v1/portal/organizations/{org_id}` deletes an organisation once no
other member is left in it, and tells each approved service to delete its
data for that organisation. In igni server's own PostgreSQL the organisation
is detached, not dropped: its database is kept, and the shipped backup goes
on backing it up (`containers/backup.sh`, header). To destroy it, drop the
database `<POSTGRES_DB>_t<n>` named in the backup `MANIFEST`. That is a
PostgreSQL operation, and it is the customer's to run.

## 4. Ending the deployment

[EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), scenario C, is the procedure:
take a final backup, export what the customer wants to keep, revoke and
remove every registered service, stop the workloads, then drop the
databases and delete the backups under the customer's own disposal
procedure. There is no single "destroy everything" command. Ignite Ember
holds nothing that needs returning or deleting.

## 5. What this page does not cover

* **CodeIndex and Artifacts keep their own data** in their own databases.
  Erasure and subject-access requests reach them through igni server
  (section 2). Their retention settings are in their own repositories.
* **The clients.** The desktop app and CLI keep configuration, credentials
  and per-project state under `~/.igni/` on each developer machine. Removing
  them is part of uninstalling the client.
* **Model weights** hold no customer data unless the customer trains on it
  with the Factory, in which case the weights, the dataset and the
  evaluation set are the customer's files ([MODEL_PROVENANCE.md](MODEL_PROVENANCE.md)).
* **A sign-in code in the logs.** With no mail relay configured, igni writes
  one-time sign-in codes to its log so an operator can hand them over. They
  expire, but they are in the customer's log store for as long as it keeps
  logs.
