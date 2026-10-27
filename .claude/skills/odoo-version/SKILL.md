---
name: odoo-version
description: >-
  Odoo 20.0 notes for this repository: what differs from the Odoo 20.0
  rules in odoo-guidelines, odoo-web-guidelines, odoo-security and
  odoo-review, plus Rteam's rules for client addon repositories. Read before
  writing, reviewing or auditing any Odoo code here.
---

# Odoo 20.0 in this repository

## Odoo 20.0

The sibling skills were written for this version: apply them as written,
with the Rteam rules and the correction below.

## Rteam rules for this repository

An Rteam Odoo repository holds custom addons for one client (or one of our own
apps) on a released Odoo version, usually deployed on Odoo.sh. It is neither
Odoo's master nor a stable branch of Odoo, so read the sibling skills with
these adjustments:

- **Stable vs master (odoo-review).** New features in our own addons are normal
  work, so the "master" reading applies to new code. Do not reformat code a
  change does not touch: keep the diff minimal and match the surrounding style.
- **Changes in a stable version (odoo-guidelines `stable.md`)** binds changes
  to Odoo's own modules, which we never edit in place: extend them from our
  addon by inheritance. For our addons already in production:
  - bump `version` in `__manifest__.py` whenever fields, views or data change:
    Odoo.sh updates a production module only when its version goes up;
  - add a migration script (`migrations/<version>/pre-*.py` or `post-*.py`)
    when existing records need values, and never rename or drop a stored field
    without one;
  - keep existing XML ids: other modules and user data reference them.
- **Review scope (odoo-review step 1).** Diff from the merge-base:
  `git diff origin/stage...HEAD` for a task branch, `git diff
  origin/main...HEAD` for a release branch. A two-dot diff against the stage
  tip shows every unreleased stage commit as removed by this change. There is
  no enterprise "other half" to look for unless the change spans two of our
  repositories.
- **Branches and commits** follow the `odoo-git` skill in this folder, not
  Odoo's own conventions.
- **Data.** Tests and demo data use invented names, amounts and documents,
  never a client's records.

## Correction for every version

- odoo-security names `setInnerHtml` among the `@web/core/utils/html`
  helpers. It does not exist in any version (checked on 18.0, 19.0, 20.0 and
  master on 2026-10-27): the helper is `setElementContent`.
