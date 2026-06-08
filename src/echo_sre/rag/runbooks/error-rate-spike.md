# Elevated Error Rate

## Symptoms
- 5xx error ratio for a service jumps above its baseline.
- Clients see failed requests; alerts like `ErrorRateHigh` / `PaymentErrorsHigh` fire.

## Diagnosis
1. Determine whether errors are generated locally or propagated from a dependency by
   reading the service's error logs (status codes, exception types, stack traces).
2. Check correlated signals: a dependency's saturation/latency, recent deploy, config or
   secret rotation, or an expired credential/certificate.
3. Segment by route, region, and version to localize the blast radius.

## Remediation
1. If a bad deploy correlates with onset, roll back immediately.
2. If a dependency is failing, fail over / shed load and apply a circuit breaker so the
   error does not cascade.
3. Fix the root dependency (e.g. database connections, downstream API quota) and verify
   the error ratio returns to baseline.

## Prevention
- Canary deploys with automatic rollback on error-budget burn.
- Health checks and circuit breakers on every cross-service call.
