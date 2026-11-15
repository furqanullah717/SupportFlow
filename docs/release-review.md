# Release review — 2026-11-15

The user authorized local execution and testing. Checks run on their Mac with Python 3.13.7 and the project's installed dependencies, against dedicated SupportFlow SQLite databases.

## Corrected issues

### Old invitations restored deactivated access

A regression reproduced this sequence: invite an email twice, accept one invitation, deactivate the member, then accept the unused invitation. Access returned unexpectedly.

Deactivation now expires pending invitations for that member. Invitation creation, acceptance and membership changes acquire the workspace lock in the same order. Acceptance rechecks validity after taking the lock. The regression now rejects the old invitation and verifies that a fresh, intentional invitation can restore access with the new role.

### Resolved chart omitted older tickets

A regression confirmed that a ticket created 40 days ago and resolved today was missing from the 30-day chart. Resolution trends now query nondeleted workspace tickets by resolution date independently of ticket creation date. The test covers older tickets, deleted tickets and workspace isolation.

## Validation

- Both regression tests failed for the reported defects before the changes and passed afterward.
- All 40 automated tests pass on SQLite, including authorization, isolation, concurrency conflicts, surveys and AI failure contracts.
- Ruff passes. `makemigrations --check --dry-run` reports no changes. Django system checks report no issues.
- Live HTTP checks pass: login, CSRF enforcement, main page routes, customer and ticket creation, editing, assignment, replies, internal notes, all three demo AI operations, search, resolution, single-use feedback, deletion and agent permission denials.
- Persistence verified through refetch and a fresh authenticated session. Dashboard 90-day total matches active ticket API count after cleanup. Assignment notifications reach the assigned agent.
- Corrected app restarted from this session's checkout at http://127.0.0.1:8000/. Fresh sign-in, dashboard, metrics, identity, ticket list and Demo AI smoke checks pass after restart.

The live checks create a QA customer and a soft-deleted QA ticket in the demo database. They do not alter unrelated databases. Automated tests use Django's separate test database.

## Limits

PostgreSQL has not been rerun on this machine. Remote AI credentials and SMTP delivery are not exercised. Replies persist inside the workspace; outbound customer email and inbound email ingestion are not implemented. Visual checks on rendered snapshots do not prove live browser form submission.
