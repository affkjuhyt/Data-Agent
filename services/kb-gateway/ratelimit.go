// Per-tenant token-bucket rate limiting (KB-SCALE-10K.md #6). One noisy tenant
// must not drain the shared pool at 10k CCU — each X-Tenant-Id gets its own
// bucket. Stdlib only (no golang.org/x/time/rate), so kb-gateway still builds
// offline. In-memory per replica; for a global limit put it in Redis, but
// per-replica buckets already cap a single tenant's fan-in to N×rps.
package main

import (
	"net/http"
	"sync"
	"time"
)

type tokenBucket struct {
	tokens float64
	last   time.Time
}

type tenantLimiter struct {
	mu      sync.Mutex
	buckets map[string]*tokenBucket
	rps     float64
	burst   float64
	maxKeys int
}

func newTenantLimiter(rps, burst float64) *tenantLimiter {
	return &tenantLimiter{
		buckets: make(map[string]*tokenBucket),
		rps:     rps,
		burst:   burst,
		maxKeys: 100_000, // crude cap; evict-all on overflow (rebuilds cheaply)
	}
}

// allow reports whether a request for `key` may proceed, consuming one token.
func (l *tenantLimiter) allow(key string) bool {
	if l.rps <= 0 { // disabled
		return true
	}
	l.mu.Lock()
	defer l.mu.Unlock()

	now := time.Now()
	b := l.buckets[key]
	if b == nil {
		if len(l.buckets) >= l.maxKeys {
			l.buckets = make(map[string]*tokenBucket) // bounded memory, not LRU
		}
		b = &tokenBucket{tokens: l.burst, last: now}
		l.buckets[key] = b
	}
	// Refill since last seen.
	b.tokens += now.Sub(b.last).Seconds() * l.rps
	if b.tokens > l.burst {
		b.tokens = l.burst
	}
	b.last = now

	if b.tokens >= 1 {
		b.tokens--
		return true
	}
	return false
}

// middleware rejects over-limit requests with 429 before they reach the backend.
func (l *tenantLimiter) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// /health is never rate-limited (readiness probes).
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		key := r.Header.Get("X-Tenant-Id")
		if key == "" {
			key = "_anon"
		}
		if !l.allow(key) {
			w.Header().Set("Retry-After", "1")
			http.Error(w, `{"error":"rate_limited","detail":"per-tenant request rate exceeded"}`,
				http.StatusTooManyRequests)
			return
		}
		next.ServeHTTP(w, r)
	})
}
