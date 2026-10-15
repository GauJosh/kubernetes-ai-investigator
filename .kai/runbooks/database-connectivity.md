# Internal database connectivity runbook

This runbook is referenced by the example knowledge pack at `.kai/knowledge.yaml`
(`internal-postgres-connectivity`). It is plain Markdown that `kai` displays a path to; `kai`
never parses or interprets its contents, and nothing in this file is executed.

## Symptom

An application container's logs or events mention a refused or failed database connection
(e.g. `connection refused`, `could not connect to server`).

## Checks

1. **Service/Endpoint reachability** -- confirm the database `Service` exists in the expected
   namespace and has at least one ready `Endpoint`/`EndpointSlice` backing it.
2. **Connection configuration** -- confirm the application's configured host, port, and
   database name match the actual database `Service` name and port, not a stale or
   environment-mismatched value.
3. **Network policy** -- check for a `NetworkPolicy` in either namespace that could be
   blocking traffic between the application and the database on the expected port.
4. **Database workload health** -- confirm the database's own Pod(s) are `Running` and
   `Ready`; a database that is itself crash-looping or still starting will also refuse new
   connections.

## Out of scope for this runbook

Diagnosing *why* the database itself is unhealthy (if step 4 finds a problem) is a separate
investigation -- run `kai pod <database-pod-name> -n <namespace>` against it directly.
