// kb-gateway — Go edge / BFF for the KB user-facing tier (see M3-CAPACITY.md §5).
// Goroutines make thousands of concurrent connections cheap (~10× less RAM/req
// than Python workers), so this is the high-CCU front door that sits behind Kong
// and in front of the Python business services.
//
// Scaffold responsibilities (stdlib only, builds offline):
//   - health
//   - request-id injection + access logging
//   - identity passthrough (Kong's X-Kong-Verified / X-User-* / Authorization)
//   - reverse-proxy to kb-backend
//   - (stub) fan-out point where a handler calls kb-backend + kb-compute and
//     merges — the reason a BFF exists.
package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
	"sync/atomic"
	"time"
)

// identity/context headers that must survive the proxy hop untouched.
var passthrough = []string{
	"X-Kong-Verified", "X-User-Id", "X-User-Roles", "X-Tenant-Id", "X-Role",
	"Authorization", "X-Request-Id",
}

var reqCounter atomic.Uint64

func withRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Request-Id") == "" {
			// cheap monotonic id (no time source needed); a real deploy uses uuid.
			r.Header.Set("X-Request-Id", "gw-"+strconv.FormatUint(reqCounter.Add(1), 10))
		}
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("%s %s rid=%s %s", r.Method, r.URL.Path,
			r.Header.Get("X-Request-Id"), time.Since(start))
	})
}

func newBackendProxy(target string) (*httputil.ReverseProxy, error) {
	u, err := url.Parse(target)
	if err != nil {
		return nil, err
	}
	p := httputil.NewSingleHostReverseProxy(u)

	// High-CCU transport. The stdlib default keeps only MaxIdleConnsPerHost=2
	// idle connections to the backend, so under thousands of concurrent users the
	// proxy churns fresh TCP connections per request → ephemeral-port exhaustion →
	// clients see bare EOF (measured: 15% EOF at 10k CCU on one box, KB-SCALE-10K.md §6).
	// Pool aggressively and let the count be tuned by env.
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConns = envInt("KB_GATEWAY_MAX_IDLE_CONNS", 4000)
	t.MaxIdleConnsPerHost = envInt("KB_GATEWAY_MAX_IDLE_PER_HOST", 2000)
	t.MaxConnsPerHost = envInt("KB_GATEWAY_MAX_CONNS_PER_HOST", 0) // 0 = unlimited
	t.IdleConnTimeout = 90 * time.Second
	p.Transport = t

	// Turn upstream failures into a recorded 502 instead of a hijacked/dropped
	// socket, so the metrics middleware counts them and clients get a clean status.
	p.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		log.Printf("proxy error rid=%s: %v", r.Header.Get("X-Request-Id"), err)
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(`{"error":"upstream unavailable"}`))
	}

	base := p.Director
	p.Director = func(r *http.Request) {
		base(r)
		r.Host = u.Host
		// passthrough headers are already on the request from the client (Kong);
		// this is the explicit list the BFF guarantees to forward.
		for _, h := range passthrough {
			if v := r.Header.Get(h); v != "" {
				r.Header.Set(h, v)
			}
		}
	}
	return p, nil
}

func envFloat(name string, def float64) float64 {
	if v := os.Getenv(name); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envInt(name string, def int) int {
	if v := os.Getenv(name); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func main() {
	addr := os.Getenv("KB_GATEWAY_ADDR")
	if addr == "" {
		addr = ":8000"
	}
	backend := os.Getenv("KB_BACKEND_URL")
	if backend == "" {
		backend = "http://kb-backend:8000"
	}
	// computeURL is where fan-out handlers would call kb-compute (see below).
	_ = os.Getenv("KB_COMPUTE_URL") // e.g. http://kb-compute:8600

	proxy, err := newBackendProxy(backend)
	if err != nil {
		log.Fatalf("bad KB_BACKEND_URL: %v", err)
	}

	// Per-tenant rate limit (0 rps = disabled). burst defaults to 2× rps.
	rps := envFloat("KB_GATEWAY_RATE_RPS", 0)
	burst := envFloat("KB_GATEWAY_RATE_BURST", rps*2)
	limiter := newTenantLimiter(rps, burst)

	// DDoS/flood guard: global rate + per-IP rate + max-concurrent (all 0 = off).
	ddos := newDDoSGuard(
		envFloat("KB_GATEWAY_GLOBAL_RPS", 0),
		envFloat("KB_GATEWAY_IP_RPS", 0),
		envInt("KB_GATEWAY_MAX_CONCURRENT", 0),
	)

	m := newMetrics()

	// Proxied traffic runs through: metrics → request-id → DDoS → per-tenant → proxy.
	// FAN-OUT SEAM: a composite handler (call kb-backend + kb-compute concurrently and
	// merge) would slot in here — the reason the BFF is Go, not Kong.
	protected := m.middleware(withRequestID(ddos.middleware(limiter.middleware(proxy))))

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok","service":"kb-gateway"}`))
	})
	mux.HandleFunc("/metrics", m.handler) // Prometheus — unprotected, unrecorded
	mux.Handle("/", protected)

	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("kb-gateway listening on %s → %s (rate=%.0f rps/tenant)", addr, backend, rps)
	if err := srv.ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}
