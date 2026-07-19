// DDoS / flood protection at the edge (KB-SCALE-10K.md #6). Complements the
// per-tenant limiter with defences that work BEFORE a tenant is even known:
//   1. a max-in-flight concurrency cap (protects against connection exhaustion),
//   2. a global request-rate ceiling (total across everyone),
//   3. a per-source-IP rate limit (one host can't flood the gateway).
// All stdlib; all opt-in (0 = disabled). Over-limit → 429; over-capacity → 503.
package main

import (
	"net"
	"net/http"
	"sync"
	"time"
)

type ddosGuard struct {
	globalMu    sync.Mutex
	global      *tokenBucket
	globalRPS   float64
	globalBurst float64
	perIP       *tenantLimiter
	sem         chan struct{}
}

func newDDoSGuard(globalRPS, ipRPS float64, maxConcurrent int) *ddosGuard {
	g := &ddosGuard{
		globalRPS:   globalRPS,
		globalBurst: globalRPS * 2,
		perIP:       newTenantLimiter(ipRPS, ipRPS*2),
	}
	if globalRPS > 0 {
		g.global = &tokenBucket{tokens: g.globalBurst, last: time.Now()}
	}
	if maxConcurrent > 0 {
		g.sem = make(chan struct{}, maxConcurrent)
	}
	return g
}

func clientIP(r *http.Request) string {
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return r.RemoteAddr
}

func (g *ddosGuard) allowGlobal() bool {
	if g.global == nil {
		return true
	}
	g.globalMu.Lock()
	defer g.globalMu.Unlock()
	now := time.Now()
	g.global.tokens += now.Sub(g.global.last).Seconds() * g.globalRPS
	if g.global.tokens > g.globalBurst {
		g.global.tokens = g.globalBurst
	}
	g.global.last = now
	if g.global.tokens >= 1 {
		g.global.tokens--
		return true
	}
	return false
}

func (g *ddosGuard) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		// 1. Concurrency cap — shed load fast when saturated.
		if g.sem != nil {
			select {
			case g.sem <- struct{}{}:
				defer func() { <-g.sem }()
			default:
				w.Header().Set("Retry-After", "1")
				http.Error(w, `{"error":"overloaded","detail":"max concurrent requests reached"}`,
					http.StatusServiceUnavailable)
				return
			}
		}
		// 2. Global rate ceiling.
		if !g.allowGlobal() {
			w.Header().Set("Retry-After", "1")
			http.Error(w, `{"error":"rate_limited","detail":"global request rate exceeded"}`,
				http.StatusTooManyRequests)
			return
		}
		// 3. Per-source-IP rate limit.
		if !g.perIP.allow(clientIP(r)) {
			w.Header().Set("Retry-After", "1")
			http.Error(w, `{"error":"rate_limited","detail":"per-IP request rate exceeded"}`,
				http.StatusTooManyRequests)
			return
		}
		next.ServeHTTP(w, r)
	})
}
