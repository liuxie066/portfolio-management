# Failure Lesson: alias services hide stale boundaries

Date: 2026-05-25

Keeping alias classes after the canonical service exists makes dependency
graphs, docs, and tests look like there are two valid owners for the same
behavior.

When an alias has no distinct behavior, delete it and update imports/tests to
the canonical owner instead of marking it as another long-lived compatibility
path.
