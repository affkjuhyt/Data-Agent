// Package rrf is a faithful Go port of the Reciprocal Rank Fusion kernel from
// services/kb_backend/pipeline/light_rag/pipeline.py:rrf_merge.
//
// A "hit" is an opaque JSON object that must carry a "point_id" string; all its
// other fields are preserved (meta pass-through) on the fused result, exactly as
// the Python dict-merge did. This is a deterministic, CPU-bound kernel — the kind
// of code M3-CAPACITY.md §5 assigns to Go.
package rrf

import "sort"

// DefaultK is the RRF constant (env RRF_K in Python; default 60).
const DefaultK = 60

// Hit mirrors the Python dict: arbitrary fields + a "point_id".
type Hit = map[string]any

// Fuse merges ranked lists into one fused, top-N list.
//
// score[pid] = Σ 1 / (k + rank)   (rank is 1-indexed within each list)
// The first-seen hit for a pid supplies the preserved meta. Ties keep first-seen
// order (Python's sorted() is stable over insertion-ordered dict keys).
func Fuse(rankedLists [][]Hit, topN, k int) []Hit {
	if k <= 0 {
		k = DefaultK
	}
	scores := make(map[string]float64)
	meta := make(map[string]Hit)
	order := make([]string, 0) // first-seen order → stable tie-break

	for _, list := range rankedLists {
		for i, hit := range list {
			rank := i + 1
			pid, _ := hit["point_id"].(string)
			if pid == "" {
				continue
			}
			if _, seen := meta[pid]; !seen {
				meta[pid] = hit
				order = append(order, pid)
			}
			scores[pid] += 1.0 / float64(k+rank)
		}
	}

	ids := make([]string, len(order))
	copy(ids, order)
	sort.SliceStable(ids, func(a, b int) bool { return scores[ids[a]] > scores[ids[b]] })

	if topN >= 0 && topN < len(ids) {
		ids = ids[:topN]
	}

	out := make([]Hit, 0, len(ids))
	for i, pid := range ids {
		h := make(Hit, len(meta[pid])+2)
		for k2, v := range meta[pid] {
			h[k2] = v
		}
		h["rrf_score"] = scores[pid]
		h["rrf_rank"] = i + 1 // 1-indexed
		out = append(out, h)
	}
	return out
}
