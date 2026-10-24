package scanpage

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fakeBackend struct {
	submitted string
}

func (f *fakeBackend) Boot() map[string]any {
	return map[string]any{"station_id": "ST-01", "scan_print_enabled": true}
}

func (f *fakeBackend) Submit(code string) map[string]any {
	f.submitted = code
	return map[string]any{"job_id": "JOB-1", "status": "queued", "dup": false, "message": ""}
}

func (f *fakeBackend) Status(jobIDs []string) map[string]any {
	return map[string]any{"jobs": []map[string]any{
		{"job_id": "JOB-1", "status": "printed_queue_ok"},
	}}
}

func (f *fakeBackend) Focused() bool { return true }

func startServer(t *testing.T) (*httptest.Server, string, *fakeBackend) {
	t.Helper()
	backend := &fakeBackend{}
	token := NewToken()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		servePage(w, r, token, backend)
	}))
	t.Cleanup(srv.Close)
	return srv, token, backend
}

func TestHealthAndGuard(t *testing.T) {
	srv, token, _ := startServer(t)
	client := &http.Client{}

	// Wrong token -> 403 (the host check passes, the token check fails).
	resp, err := client.Get(srv.URL + "/s/deadbeef")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("wrong token: got %d want 403", resp.StatusCode)
	}

	// Right token but foreign Host header -> 403 (guard runs before
	// routing; this is the DNS-rebinding vector).
	req, _ := http.NewRequest(http.MethodGet, srv.URL+"/s/"+token, nil)
	req.Host = "evil.example.com"
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("foreign host: got %d want 403", resp.StatusCode)
	}

	// Right token, loopback Host -> page served with the kiosk title marker.
	req, _ = http.NewRequest(http.MethodGet, srv.URL+"/s/"+token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("page: got %d want 200", resp.StatusCode)
	}
	if !strings.Contains(string(body), "ZhizPrint Scan Station") {
		t.Fatal("page does not contain the kiosk title marker")
	}
}

func TestSubmitStatusFocusRoundtrip(t *testing.T) {
	srv, token, backend := startServer(t)
	client := &http.Client{}
	base := srv.URL + "/s/" + token

	// Submit with a same-host Origin (what the embedded page sends).
	body, _ := json.Marshal(map[string]string{"code": "MO-9981"})
	req, _ := http.NewRequest(http.MethodPost, base+"/submit", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", base)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var envelope map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&envelope); err != nil {
		raw, _ := io.ReadAll(resp.Body)
		t.Fatalf("decode submit response (status %d body %q): %v", resp.StatusCode, raw, err)
	}
	_ = resp.Body.Close()
	if envelope["job_id"] != "JOB-1" {
		t.Fatalf("submit envelope: %#v", envelope)
	}
	if backend.submitted != "MO-9981" {
		t.Fatalf("backend got code %q", backend.submitted)
	}

	// Status roundtrip.
	req, _ = http.NewRequest(http.MethodPost, base+"/status",
		strings.NewReader(`{"job_ids":["JOB-1"]}`))
	req.Header.Set("Content-Type", "application/json")
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var status map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&status)
	_ = resp.Body.Close()
	jobs, _ := status["jobs"].([]any)
	if len(jobs) != 1 {
		t.Fatalf("status jobs: %#v", status)
	}

	// Focus endpoint.
	resp, err = client.Get(base + "/focus")
	if err != nil {
		t.Fatal(err)
	}
	var focus map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&focus)
	_ = resp.Body.Close()
	if focus["focused"] != true {
		t.Fatalf("focus: %#v", focus)
	}
}

// Guard regression: Origin from a foreign host must be rejected even with
// the right token (cross-site form post / DNS rebinding).
func TestForeignOriginRejected(t *testing.T) {
	srv, token, _ := startServer(t)
	client := &http.Client{}
	body, _ := json.Marshal(map[string]string{"code": "X"})
	req, _ := http.NewRequest(http.MethodPost, srv.URL+"/s/"+token+"/submit",
		strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "http://evil.example.com")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("foreign origin: got %d want 403", resp.StatusCode)
	}
}
