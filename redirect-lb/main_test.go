package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func staticServer(algo string, addrs ...string) *server {
	return &server{
		resolve: func() ([]string, error) { return addrs, nil },
		algo:    algo,
		status:  http.StatusTemporaryRedirect,
	}
}

func redirectHost(loc string) string {
	return strings.SplitN(strings.TrimPrefix(loc, "http://"), "/", 2)[0]
}

func TestRedirect307PreservesPathAndQuery(t *testing.T) {
	s := staticServer("roundrobin", "backend-1:8080")
	w := httptest.NewRecorder()
	s.handleRedirect(w, httptest.NewRequest(http.MethodPost, "/api/v1/payments?x=1", nil))

	if w.Code != http.StatusTemporaryRedirect {
		t.Fatalf("want 307, got %d", w.Code)
	}
	if loc := w.Header().Get("Location"); loc != "http://backend-1:8080/api/v1/payments?x=1" {
		t.Fatalf("unexpected Location: %q", loc)
	}
}

func TestNoBackendsReturns503(t *testing.T) {
	s := staticServer("roundrobin") // resolve returns empty
	w := httptest.NewRecorder()
	s.handleRedirect(w, httptest.NewRequest(http.MethodGet, "/", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("want 503 with no backends, got %d", w.Code)
	}
}

func TestRoundRobinCyclesEvenly(t *testing.T) {
	s := staticServer("roundrobin")
	addrs := []string{"a", "b", "c"}
	counts := map[string]int{}
	for range 30 {
		counts[s.pick(addrs)]++
	}
	for _, a := range addrs {
		if counts[a] != 10 {
			t.Fatalf("round robin uneven: %v", counts)
		}
	}
}

func TestRandomStaysInSet(t *testing.T) {
	s := staticServer("random")
	addrs := []string{"a", "b"}
	for range 50 {
		if got := s.pick(addrs); got != "a" && got != "b" {
			t.Fatalf("random picked out-of-set: %q", got)
		}
	}
}

func TestRedirectsSpreadAcrossBackends(t *testing.T) {
	s := staticServer("roundrobin", "backend-1:8080", "backend-2:8080", "backend-3:8080")
	seen := map[string]bool{}
	for range 9 {
		w := httptest.NewRecorder()
		s.handleRedirect(w, httptest.NewRequest(http.MethodGet, "/", nil))
		seen[redirectHost(w.Header().Get("Location"))] = true
	}
	if len(seen) != 3 {
		t.Fatalf("want spread across 3 backends, saw %v", seen)
	}
}

func TestStatusEndpoint(t *testing.T) {
	s := staticServer("roundrobin", "a:8080", "b:8080")
	w := httptest.NewRecorder()
	s.handleStatus(w, httptest.NewRequest(http.MethodGet, "/__lb/backends", nil))

	var out []map[string]string
	if err := json.NewDecoder(w.Body).Decode(&out); err != nil {
		t.Fatalf("decode status: %v", err)
	}
	if len(out) != 2 || out[0]["addr"] == "" {
		t.Fatalf("unexpected status payload: %+v", out)
	}
}

func TestSplitCSV(t *testing.T) {
	got := splitCSV(" a:1 , b:2 ,, ")
	if len(got) != 2 || got[0] != "a:1" || got[1] != "b:2" {
		t.Fatalf("splitCSV: %v", got)
	}
	if len(splitCSV("")) != 0 {
		t.Fatal("empty string should yield no backends")
	}
}
