# Release review — 2026-11-15

Reviewed implementation commit `8f108a3`. This is a source review, not a completed QA pass. Application source has not changed during this handover.

## Release blockers

### Deactivation can be bypassed through an unused invitation

Severity: high. Source-grounded; runtime confirmation pending.

1. Invite one email twice before either invitation is accepted.
2. Accept the first invitation.
3. As admin, deactivate that member.
4. As that user, accept the second invitation.

`create_invitation` allows multiple pending invitations. `update_member` does not revoke them. `accept_invitation` reactivates an inactive membership with the stored invitation role. Access removal therefore does not reliably revoke old invitations.

Required correction: revoke pending invitations for a member when deactivating them; serialize membership changes and invitation acceptance consistently. Add a regression proving an old invitation cannot restore removed access, while a fresh intentional invitation can.

### Resolved chart omits older tickets

Severity: medium. Source-grounded; runtime confirmation pending.

1. Create a ticket dated 40 days ago.
2. Resolve it today.
3. Select the 30-day dashboard window.

`dashboard_data` derives the resolved series from tickets created inside the period. The resolution is absent. Query resolution timestamps independently from ticket creation timestamps, while preserving workspace isolation and excluding deleted tickets. Add a regression for an older ticket resolved inside the period.

## Scope limits

Replies persist in the workspace. No customer email delivery or inbound email channel exists. Invitations and password reset use configurable email, with console output by default. Remote AI requires provider credentials; demo AI is visibly labeled.

## Validation state

The earlier implementation session reported 38 passing SQLite tests and HTTP workflow checks at `8f108a3`. Those results are historical, not a new run. Final PostgreSQL validation and the complete browser reply workflow were not completed there.

A subsequent QA session recorded a declined full-suite/lint command (approval request `hir_4d5aeacd52944547a0661d4aeb5ab533`). This handover preserves that refusal and requests fresh authorization before running those checks. Do not describe QA as complete until permission is granted, the issues are reproduced and corrected, and critical flows pass.

GET `http://127.0.0.1:8000/health/` returned 200 during this handover. The existing local demo server remains available. Documentation files and code fences were checked, and `git diff --check` passed.
