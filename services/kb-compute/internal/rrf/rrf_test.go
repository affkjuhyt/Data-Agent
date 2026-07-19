package rrf

import (
	"math"
	"testing"
)

func approx(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

// Two lists, one overlapping pid. Verify the exact RRF math + meta pass-through +
// 1-indexed ranks, matching the Python rrf_merge.
func TestFuse_ScoresAndOrder(t *testing.T) {
	k := 60
	lists := [][]Hit{
		{{"point_id": "a", "text": "A"}, {"point_id": "b", "text": "B"}},
		{{"point_id": "b", "text": "B2"}, {"point_id": "c", "text": "C"}},
	}
	out := Fuse(lists, 10, k)

	// b appears at rank 2 (list0) and rank 1 (list1): 1/62 + 1/61  ← highest
	// a: 1/61 ; c: 1/62
	want := map[string]float64{
		"a": 1.0 / 61,
		"b": 1.0/62 + 1.0/61,
		"c": 1.0 / 62,
	}
	if len(out) != 3 {
		t.Fatalf("want 3 fused hits, got %d", len(out))
	}
	if out[0]["point_id"] != "b" {
		t.Fatalf("want 'b' first (highest score), got %v", out[0]["point_id"])
	}
	for _, h := range out {
		pid := h["point_id"].(string)
		if !approx(h["rrf_score"].(float64), want[pid]) {
			t.Errorf("pid %s: score %v want %v", pid, h["rrf_score"], want[pid])
		}
	}
	// meta pass-through: first-seen "b" had text "B" (not "B2").
	if out[0]["text"] != "B" {
		t.Errorf("meta should keep first-seen hit: want text=B, got %v", out[0]["text"])
	}
	// ranks are 1-indexed and contiguous.
	for i, h := range out {
		if h["rrf_rank"].(int) != i+1 {
			t.Errorf("rrf_rank at %d = %v, want %d", i, h["rrf_rank"], i+1)
		}
	}
}

func TestFuse_TopNAndSkipsMissingID(t *testing.T) {
	lists := [][]Hit{
		{{"point_id": "a"}, {"no_id": true}, {"point_id": "b"}, {"point_id": "c"}},
	}
	out := Fuse(lists, 2, 0) // k=0 → DefaultK
	if len(out) != 2 {
		t.Fatalf("top_n=2 → want 2, got %d", len(out))
	}
	// The id-less hit is skipped; a (rank1) beats b (rank3) beats c (rank4).
	if out[0]["point_id"] != "a" || out[1]["point_id"] != "b" {
		t.Errorf("want [a b], got [%v %v]", out[0]["point_id"], out[1]["point_id"])
	}
}
