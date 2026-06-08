# Database Connection Pool Exhaustion

## Symptoms
- Upstream services report rising p95/p99 latency with no change in request volume.
- Application logs show "timeout acquiring connection", "pool exhausted", or
  "context deadline exceeded" when calling the database.
- The database logs "FATAL: too many connections" or "remaining connection slots are reserved".
- `pg_connections_active` (or equivalent) sits at or near `max_connections`.

## Diagnosis
1. Confirm the deepest unhealthy dependency by walking the service topology from the
   alerting service toward its data stores.
2. Compare active DB connections against the configured `max_connections`.
3. Correlate the onset time of the latency anomaly with deploys, traffic shifts, or a
   long-running transaction / migration.

## Remediation
1. Shed load: scale the saturated service's replicas or temporarily lower its per-pod
   pool size so the fleet total stays under `max_connections`.
2. Add or right-size a connection pooler (e.g. PgBouncer in transaction mode) so total
   server-side connections are bounded regardless of client count.
3. Find and kill long-running / idle-in-transaction sessions holding connections.
4. If sustained, raise `max_connections` only with enough memory headroom (each
   connection costs memory), and review slow queries that hold connections too long.

## Prevention
- Cap client pool sizes such that (replicas x pool_size) < max_connections.
- Alert on connection saturation (> 80% of max) before latency degrades.
