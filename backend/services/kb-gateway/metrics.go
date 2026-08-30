// Prometheus /metrics for the gateway (docs/KB-SCALE-10K.md #8). Hand-rolled text
// exposition so the binary stays stdlib-only. Captures the edge signals that
// matter for CCU: request rate by status, in-flight, latency histogram, and
// how many requests the DDoS/rate guards rejected.
package main

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

var durationBucketsMs = []float64{5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000}

type metrics struct {
	mu           sync.Mutex
	statusCounts map[int]uint64   // requests by HTTP status
	rejects      map[string]uint64 // rejections by reason (ratelimit_tenant / ratelimit_ip / ratelimit_global / overload)
	bucketCounts []uint64          // cumulative-ready duration histogram
	sumMs        float64
	count        uint64
	inflight     int64
}

func newMetrics() *metrics {
	return &metrics{
		statusCounts: make(map[int]uint64),
		rejects:      make(map[string]uint64),
		bucketCounts: make([]uint64, len(durationBucketsMs)),
	}
}

func (m *metrics) reject(reason string) {
	m.mu.Lock()
	m.rejects[reason]++
	m.mu.Unlock()
}

func (m *metrics) observe(status int, durMs float64) {
	m.mu.Lock()
	m.statusCounts[status]++
	m.sumMs += durMs
	m.count++
	for i, b := range durationBucketsMs {
		if durMs <= b {
			m.bucketCounts[i]++
		}
	}
	m.mu.Unlock()
}

// statusRecorder captures the response status code for metrics.
type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// middleware wraps the whole chain: counts in-flight + records status + duration.
func (m *metrics) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/metrics" || r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		atomic.AddInt64(&m.inflight, 1)
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: 200}
		next.ServeHTTP(rec, r)
		atomic.AddInt64(&m.inflight, -1)
		m.observe(rec.status, float64(time.Since(start).Microseconds())/1000.0)
	})
}

func (m *metrics) handler(w http.ResponseWriter, _ *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")

	fmt.Fprintf(w, "# TYPE kb_gateway_requests_total counter\n")
	statuses := make([]int, 0, len(m.statusCounts))
	for s := range m.statusCounts {
		statuses = append(statuses, s)
	}
	sort.Ints(statuses)
	for _, s := range statuses {
		fmt.Fprintf(w, "kb_gateway_requests_total{status=\"%d\"} %d\n", s, m.statusCounts[s])
	}

	fmt.Fprintf(w, "# TYPE kb_gateway_rejected_total counter\n")
	for reason, c := range m.rejects {
		fmt.Fprintf(w, "kb_gateway_rejected_total{reason=\"%s\"} %d\n", reason, c)
	}

	fmt.Fprintf(w, "# TYPE kb_gateway_inflight gauge\n")
	fmt.Fprintf(w, "kb_gateway_inflight %d\n", atomic.LoadInt64(&m.inflight))

	fmt.Fprintf(w, "# TYPE kb_gateway_request_duration_ms histogram\n")
	for i, b := range durationBucketsMs {
		fmt.Fprintf(w, "kb_gateway_request_duration_ms_bucket{le=\"%s\"} %d\n",
			strconv.FormatFloat(b, 'f', -1, 64), m.bucketCounts[i])
	}
	fmt.Fprintf(w, "kb_gateway_request_duration_ms_bucket{le=\"+Inf\"} %d\n", m.count)
	fmt.Fprintf(w, "kb_gateway_request_duration_ms_sum %s\n", strconv.FormatFloat(m.sumMs, 'f', 1, 64))
	fmt.Fprintf(w, "kb_gateway_request_duration_ms_count %d\n", m.count)
}
