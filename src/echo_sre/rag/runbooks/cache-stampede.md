# Cache Stampede / Thundering Herd

## Symptoms
- Sudden latency and load spike on a backing store (database, downstream API) right after
  a cache flush, mass key expiry, or a cache node restart.
- Redis/memcached hit ratio drops sharply; database QPS spikes in lockstep.

## Diagnosis
1. Correlate the database/backend load spike with cache hit-ratio collapse and any cache
   deploy, eviction, or TTL expiry event.
2. Confirm many concurrent requests are recomputing the same expensive key.

## Remediation
1. Add request coalescing / single-flight so only one worker recomputes a missing key.
2. Use jittered TTLs and early/background refresh to avoid synchronized expiry.
3. Temporarily serve stale-on-error while the cache repopulates.

## Prevention
- Pre-warm caches on deploy; stagger TTLs; cap backend concurrency per key.
