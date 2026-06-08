# High Request Latency

## Symptoms
- p95/p99 latency rises on a user-facing service (gateway, checkout, API).
- Throughput is flat or down while latency climbs (i.e. not a simple traffic spike).

## Diagnosis
1. Identify the slow service from the alert, then follow its dependency graph downstream;
   latency usually originates at the deepest slow hop, not where it is first observed.
2. For each hop check: saturation (CPU, memory, connections, thread pools), errors, and
   queueing. A downstream timeout often manifests as upstream latency + retries.
3. Inspect logs at the suspected hop for timeouts, retries, GC pauses, or lock waits.

## Remediation
1. Relieve the saturated resource (scale out, increase pool/limit, shed load).
2. Roll back a recent deploy if the onset correlates with a release.
3. Add backpressure / timeouts / circuit breakers so a slow dependency degrades
   gracefully instead of amplifying latency upstream.

## Prevention
- Set SLOs and alert on latency burn-rate, not just static thresholds.
- Load-test the critical path and keep dependency timeouts shorter than client timeouts.
