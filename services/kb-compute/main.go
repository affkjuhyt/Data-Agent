// kb-compute — Go service for the CPU-bound, deterministic KB kernels
// (RRF fusion today; BM25 / entity-dedup / graph-build next). See
// M3-CAPACITY.md §5 for the Go/Python split. Python (kb-backend, worker) calls
// this instead of doing the fusion in-process.
//
// Scaffold transport is HTTP+JSON (stdlib only, builds offline). The target
// gRPC contract lives in libs/proto/kbcompute.proto.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/dataagent/kb-compute/internal/rrf"
)

type rrfRequest struct {
	Lists [][]rrf.Hit `json:"lists"`
	TopN  int         `json:"top_n"`
	K     int         `json:"k"`
}

type rrfResponse struct {
	Hits []rrf.Hit `json:"hits"`
}

func handleRRF(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req rrfRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}
	topN := req.TopN
	if topN == 0 {
		topN = -1 // 0 in the request means "no explicit cap" → return all
	}
	fused := rrf.Fuse(req.Lists, topN, req.K)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(rrfResponse{Hits: fused})
}

func handleHealth(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"status":"ok","service":"kb-compute"}`))
}

func main() {
	addr := os.Getenv("KB_COMPUTE_ADDR")
	if addr == "" {
		addr = ":8600"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/rrf-fuse", handleRRF)

	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("kb-compute listening on %s", addr)
	if err := srv.ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}
