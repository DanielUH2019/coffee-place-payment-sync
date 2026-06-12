// Command lb is a minimal redirect (HTTP 307/302) load balancer for the hw1
// payments app.
//
// How it works: on every request it asks Docker's embedded DNS for the current
// replicas of a service (one A record per running container), picks one, and
// answers with a redirect whose Location points straight at that backend. The
// client then talks to the backend directly.
//
// Deliberately tiny: discovery is a per-request net.LookupHost (no background
// table, no locks), and there is no active health check — a stopped replica
// simply leaves Docker DNS, so the next lookup doesn't return it. That is the
// whole self-healing story. See redirect-lb-plan.md §4 for the trade-off.
package main

import (
	"encoding/json"
	"log"
	"math/rand/v2"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
)

const scheme = "http" // backends are plain HTTP

// resolver returns the current backend addresses as host:port.
type resolver func() ([]string, error)

type server struct {
	resolve resolver
	algo    string // "roundrobin" (default) | "random"
	status  int    // 307 (default, preserves POST body) | 302
	counter atomic.Uint64
}

// pick selects one backend. Round-robin uses an atomic counter; the address
// list is sorted (see dnsResolver) so the counter maps to a stable position
// even though Docker DNS rotates the order it returns records in.
func (s *server) pick(addrs []string) string {
	if len(addrs) == 0 {
		return ""
	}
	if s.algo == "random" || s.algo == "rand" {
		return addrs[rand.IntN(len(addrs))]
	}
	n := s.counter.Add(1) - 1
	return addrs[n%uint64(len(addrs))]
}

func (s *server) handleRedirect(w http.ResponseWriter, r *http.Request) {
	addrs, err := s.resolve()
	if err != nil {
		log.Printf("resolve error: %v", err)
	}
	target := s.pick(addrs)
	if target == "" {
		http.Error(w, "no backends available", http.StatusServiceUnavailable)
		return
	}
	http.Redirect(w, r, scheme+"://"+target+r.URL.RequestURI(), s.status)
}

// handleStatus reports the live backend set as JSON — handy for the demo and
// the integration tests (membership is observable without sampling redirects).
func (s *server) handleStatus(w http.ResponseWriter, r *http.Request) {
	addrs, _ := s.resolve()
	out := make([]map[string]string, 0, len(addrs))
	for _, a := range addrs {
		out = append(out, map[string]string{"addr": a})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// dnsResolver resolves a service name to one address per A record — exactly how
// a scaled Docker Compose service (or a K8s headless Service) exposes replicas.
// The result is sorted for deterministic round-robin.
func dnsResolver(service, port string) resolver {
	return func() ([]string, error) {
		ips, err := net.LookupHost(service)
		if err != nil {
			return nil, err
		}
		out := make([]string, 0, len(ips))
		for _, ip := range ips {
			out = append(out, net.JoinHostPort(ip, port))
		}
		sort.Strings(out)
		return out, nil
	}
}

func main() {
	listen := env("LB_LISTEN", ":8090")
	service := env("LB_BACKEND_SERVICE", "external-app")
	port := env("LB_BACKEND_PORT", "8080")
	algo := env("LB_ALGO", "roundrobin")
	status := envInt("LB_REDIRECT_STATUS", http.StatusTemporaryRedirect)

	srv := &server{resolve: dnsResolver(service, port), algo: algo, status: status}
	mode := "DNS service " + service + ":" + port
	if static := splitCSV(env("LB_BACKENDS", "")); len(static) > 0 {
		srv.resolve = func() ([]string, error) { return static, nil }
		mode = "static list"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/__lb/backends", srv.handleStatus)
	mux.HandleFunc("/", srv.handleRedirect)

	log.Printf("redirect-lb listening on %s | backends=%s | algo=%s | redirect=%d",
		listen, mode, algo, status)
	log.Fatal(http.ListenAndServe(listen, mux))
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

func splitCSV(s string) []string {
	out := []string{}
	for p := range strings.SplitSeq(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
