// Package scanpage serves the embedded scan workstation page on the
// loopback interface (plan 2/5): 127.0.0.1:<port> only, a random per-start
// token in the URL path, Host + Origin double-check, so a malicious web
// page in another browser tab cannot drive local prints (DNS-rebinding /
// CSRF hardening).
package scanpage

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"time"
)

//go:embed page.html
var pageHTML []byte

// PageTitle is both the HTML <title> and the focus-watchdog marker: the
// kiosk window shows this title, and Focused() implementations compare it
// against the foreground window title.
const PageTitle = "ZhizPrint Scan Station"

// Backend is the agent state the page talks to (implemented by core.Agent).
type Backend interface {
	// Boot returns the page bootstrap payload (station, switches, version).
	Boot() map[string]any
	// Submit forwards one scanned code through the ERP shared trunk.
	Submit(code string) map[string]any
	// Status returns local job views for the polled job ids.
	Status(jobIDs []string) map[string]any
	// Focused reports whether the kiosk window owns the foreground.
	Focused() bool
}

// NewToken returns a fresh 128-bit hex token for the page URL.
func NewToken() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		panic(err) // crypto/rand failure is unrecoverable
	}
	return hex.EncodeToString(buf)
}

// Serve binds addr (must be loopback), starts the HTTP server in a
// goroutine and returns the bound address.
func Serve(addr, token string, b Backend) (net.Addr, error) {
	if token == "" {
		return nil, errors.New("scanpage: empty page token")
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, err
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]any{
			"ok": true, "agent": "ZhizPrintAgent", "page_title": PageTitle,
		})
	})
	mux.HandleFunc("/s/", func(w http.ResponseWriter, r *http.Request) {
		servePage(w, r, token, b)
	})
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("[scanpage] server stopped: %v", err)
		}
	}()
	return ln.Addr(), nil
}

// Shutdown gracefully stops the server (unused in v1 shutdown path, kept
// for symmetry).
func Shutdown(srv *http.Server) error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	return srv.Shutdown(ctx)
}

func servePage(w http.ResponseWriter, r *http.Request, token string, b Backend) {
	if !guardRequest(w, r) {
		return
	}
	rest := strings.TrimPrefix(r.URL.Path, "/s/")
	route, sub, hasSub := strings.Cut(rest, "/")
	if !tokenOK(route, token) {
		forbidden(w)
		return
	}
	if !hasSub {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		_, _ = w.Write(pageHTML)
		return
	}
	switch sub {
	case "boot":
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		writeJSON(w, b.Boot())
	case "submit":
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var body struct {
			Code string `json:"code"`
		}
		if !decodeBody(w, r, &body) {
			return
		}
		writeJSON(w, b.Submit(body.Code))
	case "status":
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var body struct {
			JobIDs []string `json:"job_ids"`
		}
		if !decodeBody(w, r, &body) {
			return
		}
		writeJSON(w, b.Status(body.JobIDs))
	case "focus":
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		writeJSON(w, map[string]any{"focused": b.Focused()})
	default:
		http.Error(w, "not found", http.StatusNotFound)
	}
}

// guardRequest enforces the loopback contract: Host must be 127.0.0.1
// (any port), Origin/Referer when present must point at 127.0.0.1.
// Violations answer 403 without echoing why.
func guardRequest(w http.ResponseWriter, r *http.Request) bool {
	host := r.Host
	if i := strings.LastIndex(host, ":"); i >= 0 && !strings.Contains(host, "]") {
		host = host[:i]
	}
	if host != "127.0.0.1" {
		forbidden(w)
		return false
	}
	for _, header := range []string{"Origin", "Referer"} {
		v := r.Header.Get(header)
		if v == "" {
			continue
		}
		// Extract host from "scheme://host[:port]/path..." : strip the
		// scheme, cut the path FIRST, then the port off the host-only
		// remainder (port-first would miss every origin that carries a
		// path, which is exactly what browsers send for same-origin
		// fetch() with a URL that includes one).
		originHost := v
		if i := strings.Index(originHost, "://"); i >= 0 {
			originHost = originHost[i+3:]
		}
		if i := strings.Index(originHost, "/"); i >= 0 {
			originHost = originHost[:i]
		}
		if i := strings.LastIndex(originHost, ":"); i >= 0 && !strings.Contains(originHost, "]") {
			originHost = originHost[:i]
		}
		if originHost != "127.0.0.1" {
			forbidden(w)
			return false
		}
	}
	return true
}

// tokenOK compares the presented path token in constant time.
func tokenOK(presented, token string) bool {
	return len(presented) == len(token) &&
		subtle.ConstantTimeCompare([]byte(presented), []byte(token)) == 1
}

func decodeBody(w http.ResponseWriter, r *http.Request, dst any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(dst); err != nil && !errors.Is(err, io.EOF) {
		http.Error(w, "bad json body", http.StatusBadRequest)
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	enc := json.NewEncoder(w)
	_ = enc.Encode(payload)
}

func forbidden(w http.ResponseWriter) {
	http.Error(w, "forbidden", http.StatusForbidden)
}
