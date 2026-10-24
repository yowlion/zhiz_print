// Package erpclient implements the five signed calls the ZhizPrintAgent
// makes against the ERP (zhiz_print/api/agent.py):
//
//	register / poll / ack / heartbeat  -> HMAC(api_key+api_secret)
//	download                           -> short-TTL token URL (no HMAC)
//
// Signing contract (zhiz_print/api/scan_print.py _canonical_body): POST
// Content-Type application/json, every value a JSON string, body signed as
// hex(HMAC_SHA256(api_secret, timestamp + "." + canonical_json)) where the
// canonical form is Python json.dumps(sort_keys, separators, no-ascii-escape)
// over every param except "sig". The server rebuilds that exact string, so
// encoding differences break auth - see canonical.go.
//
// Clock protocol: every response carries server_time ("%Y-%m-%d %H:%M:%S",
// server wall clock). The agent derives an offset (assumes one timezone
// across the deployment - plan 6-2), persists it, and stamps every request
// with local epoch + offset so the +/-300s window holds on drifted shop PCs.
// A 401 triggers an offset resync (register against candidate offsets) and
// one replay of the failed call.
package erpclient

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	registerMethod   = "zhiz_print.api.agent.agent_register"
	pollMethod       = "zhiz_print.api.agent.agent_poll"
	ackMethod        = "zhiz_print.api.agent.agent_ack"
	heartbeatMethod  = "zhiz_print.api.agent.agent_heartbeat"
	scanMethod       = "zhiz_print.api.scan_print.scan_receive"
	apiMethodPrefix  = "/api/method/"
	maxResponseBytes = 32 << 20 // 32 MiB; poll bodies can inline ~2MB+ b64 PDFs
)

// PollLimit caps one claim batch; the server hard-caps at 20
// (api/agent.py MAX_POLL_LIMIT).
const PollLimit = 10

// ErrNotFound is returned by Download for an expired / consumed token
// (server answers 403 text for a dead token).
var ErrNotFound = errors.New("download rejected (invalid, expired or consumed token)")

// Client is a signed ERP session for one station.
type Client struct {
	BaseURL string // e.g. https://erp.example.com
	APIKey  string
	Secret  string
	AgentID string // may be empty; the server assigns "agent-<station>"
	Version string // agent version reported with every call
	HTTP    *http.Client

	// onOffsetChange fires whenever the measured clock offset differs from
	// the previous value so the caller can persist it (config.json).
	onOffsetChange func(offsetSec int64)

	mu     sync.Mutex
	offset int64
}

// New builds a client. proxy is an explicit config override; when empty the
// transport honours the standard HTTP(S)_PROXY environment variables.
func New(baseURL, apiKey, secret, agentID, version, proxy string,
	onOffsetChange func(offsetSec int64)) (*Client, error) {
	if strings.TrimSpace(baseURL) == "" {
		return nil, errors.New("erp_base_url is empty")
	}
	if apiKey == "" || secret == "" {
		return nil, errors.New("api_key / api_secret are empty")
	}
	transport := &http.Transport{}
	if strings.TrimSpace(proxy) != "" {
		u, err := url.Parse(proxy)
		if err != nil {
			return nil, fmt.Errorf("http_proxy: %w", err)
		}
		transport.Proxy = func(*http.Request) (*url.URL, error) { return u, nil }
	} else {
		transport.Proxy = http.ProxyFromEnvironment
	}
	return &Client{
		BaseURL:        strings.TrimRight(baseURL, "/"),
		APIKey:         apiKey,
		Secret:         secret,
		AgentID:        strings.TrimSpace(agentID),
		Version:        version,
		HTTP:           &http.Client{Timeout: 30 * time.Second, Transport: transport},
		onOffsetChange: onOffsetChange,
	}, nil
}

// ClockOffset returns the current persisted-in-memory server clock offset.
func (c *Client) ClockOffset() int64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.offset
}

// SetClockOffset overrides the offset (config bootstrap) without firing the
// persistence callback.
func (c *Client) SetClockOffset(sec int64) {
	c.mu.Lock()
	c.offset = sec
	c.mu.Unlock()
}

func (c *Client) applyServerTime(serverTime string) {
	off, ok := OffsetFromServerTime(serverTime)
	if !ok {
		return
	}
	c.mu.Lock()
	changed := off != c.offset
	c.offset = off
	cb := c.onOffsetChange
	c.mu.Unlock()
	if changed && cb != nil {
		cb(off)
	}
}

// OffsetFromServerTime derives "server - local" in seconds from a
// "%Y-%m-%d %H:%M:%S" wall clock. Both clocks are compared as naive local
// wall times mapped onto UTC, which is exact when agent and server share
// the same timezone offset - the single-region deployment assumption of the
// scan-print plan (plan 6). Parse failures return ok=false (caller keeps
// the previous offset).
func OffsetFromServerTime(serverTime string) (int64, bool) {
	t, err := time.ParseInLocation("2006-01-02 15:04:05", strings.TrimSpace(serverTime), time.Local)
	if err != nil {
		return 0, false
	}
	return t.Unix() - time.Now().Unix(), true
}

// ---------------------------------------------------------------------------
// Signed transport
// ---------------------------------------------------------------------------

// post builds, signs and sends one request; it returns the raw body and
// HTTP status so signedCall can decide about resync + error extraction.
func (c *Client) post(ctx context.Context, method string, params map[string]string,
	offsetOverride int64) ([]byte, int, error) {
	ts := time.Now().Unix() + c.ClockOffset()
	if offsetOverride != math.MinInt64 {
		ts = time.Now().Unix() + offsetOverride
	}
	body := make(map[string]string, len(params)+3)
	for k, v := range params {
		body[k] = v
	}
	body["api_key"] = c.APIKey
	body["timestamp"] = strconv.FormatInt(ts, 10)
	body["sig"] = Sign(c.Secret, strconv.FormatInt(ts, 10), CanonicalJSON(body))

	wire, err := json.Marshal(body) // wire encoding is free-form; only the canonical copy is signed
	if err != nil {
		return nil, 0, err
	}
	endpoint := c.BaseURL + apiMethodPrefix + method
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(wire))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
	if err != nil {
		return nil, resp.StatusCode, err
	}
	return data, resp.StatusCode, nil
}

// signedCall posts with the current offset, and on a 401 (signature or
// timestamp window rejected) resyncs the clock and replays once.
func (c *Client) signedCall(ctx context.Context, method string,
	params map[string]string) (map[string]any, error) {
	data, status, err := c.post(ctx, method, params, math.MinInt64)
	if err == nil && status == http.StatusUnauthorized {
		if resyncErr := c.resync(ctx); resyncErr == nil {
			data, status, err = c.post(ctx, method, params, math.MinInt64)
		}
	}
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, c.frappeError(method, status, data)
	}
	var wrap struct {
		Message map[string]any `json:"message"`
	}
	if err := json.Unmarshal(data, &wrap); err != nil || wrap.Message == nil {
		return nil, fmt.Errorf("erp %s: unexpected response envelope", method)
	}
	if st, ok := wrap.Message["server_time"].(string); ok {
		c.applyServerTime(st)
	}
	return wrap.Message, nil
}

// resync tries the stored offset and then 0 (local clock authoritative);
// whichever candidate authenticates becomes the new offset. Both candidates
// fail when credentials are wrong - that surfaces as an error.
func (c *Client) resync(ctx context.Context) error {
	params := map[string]string{"agent_version": c.Version}
	if c.AgentID != "" {
		params["agent_id"] = c.AgentID
	}
	for _, candidate := range []int64{c.ClockOffset(), 0} {
		data, status, err := c.post(ctx, registerMethod, params, candidate)
		if err != nil || status != http.StatusOK {
			continue
		}
		var wrap struct {
			Message map[string]any `json:"message"`
		}
		if json.Unmarshal(data, &wrap) != nil || wrap.Message == nil {
			continue
		}
		if st, ok := wrap.Message["server_time"].(string); ok {
			if off, ok2 := OffsetFromServerTime(st); ok2 {
				c.mu.Lock()
				changed := off != c.offset
				c.offset = off
				cb := c.onOffsetChange
				c.mu.Unlock()
				if changed && cb != nil {
					cb(off)
				}
			}
		}
		return nil
	}
	return errors.New("clock resync failed (check api_key / api_secret / server clock)")
}

// frappeError extracts a human-readable message from a Frappe error body:
// "_server_messages" is a double-encoded JSON array of {message}, falling
// back to the tail of the "exc" traceback, then the HTTP status.
func (c *Client) frappeError(method string, status int, body []byte) error {
	var raw struct {
		ServerMessages string `json:"_server_messages"`
		Exc            string `json:"exc"`
		Exception      string `json:"exception"`
	}
	_ = json.Unmarshal(body, &raw)
	if raw.ServerMessages != "" {
		var items []map[string]any
		if json.Unmarshal([]byte(raw.ServerMessages), &items) == nil {
			parts := make([]string, 0, len(items))
			for _, it := range items {
				if m, ok := it["message"].(string); ok && m != "" {
					parts = append(parts, m)
				}
			}
			if len(parts) > 0 {
				return fmt.Errorf("erp %s: HTTP %d: %s", method, status, strings.Join(parts, "; "))
			}
		}
	}
	if line := lastMeaningfulLine(raw.Exc); line != "" {
		return fmt.Errorf("erp %s: HTTP %d: %s", method, status, line)
	}
	if raw.Exception != "" {
		return fmt.Errorf("erp %s: HTTP %d: %s", method, status, raw.Exception)
	}
	return fmt.Errorf("erp %s: HTTP %d", method, status)
}

func lastMeaningfulLine(text string) string {
	text = strings.TrimSpace(text)
	if text == "" {
		return ""
	}
	lines := strings.Split(text, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if s := strings.TrimSpace(lines[i]); s != "" {
			return s
		}
	}
	return ""
}

// ---------------------------------------------------------------------------
// Value coercion helpers (Frappe JSON gives any as float64 / string / bool)
// ---------------------------------------------------------------------------

func anyToInt(v any) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case string:
		n2, _ := strconv.Atoi(strings.TrimSpace(n))
		return n2
	case bool:
		if n {
			return 1
		}
		return 0
	}
	return 0
}

func anyToStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func anyToBool(v any) bool {
	switch b := v.(type) {
	case bool:
		return b
	case float64:
		return b != 0
	case string:
		return b == "1" || strings.EqualFold(b, "true")
	}
	return false
}

// VersionBelow mirrors api/agent.py _version_below: numeric dot comparison
// (Python tuple semantics - shorter prefix compares less), unparseable
// values never block (an agent with a weird version string must keep
// working).
func VersionBelow(actual, minimum string) bool {
	have := versionParts(actual)
	want := versionParts(minimum)
	if have == nil || want == nil {
		return false
	}
	for i := 0; i < len(have) && i < len(want); i++ {
		if have[i] != want[i] {
			return have[i] < want[i]
		}
	}
	return len(have) < len(want)
}

func versionParts(v string) []int {
	var parts []int
	for _, chunk := range strings.Split(strings.TrimSpace(v), ".") {
		digits := ""
		for _, r := range chunk {
			if r >= '0' && r <= '9' {
				digits += string(r)
			} else {
				break
			}
		}
		if digits == "" {
			return nil
		}
		n, err := strconv.Atoi(digits)
		if err != nil {
			return nil
		}
		parts = append(parts, n)
	}
	if len(parts) == 0 {
		return nil
	}
	return parts
}

// ---------------------------------------------------------------------------
// Typed endpoint wrappers
// ---------------------------------------------------------------------------

// RegisterResult is the agent_register response (bootstrap config).
type RegisterResult struct {
	AgentID              string
	Status               string // online | version_low
	MinAgentVersion      string
	PollIntervalSec      int
	InlineThresholdBytes int
	ScanPrintEnabled     bool
	StationUser          string
}

// Register performs the login/bootstrap call.
func (c *Client) Register(ctx context.Context, printersStatus string, queueDepth int) (*RegisterResult, error) {
	params := map[string]string{
		"agent_version": c.Version,
		"queue_depth":   strconv.Itoa(queueDepth),
	}
	if c.AgentID != "" {
		params["agent_id"] = c.AgentID
	}
	if printersStatus != "" {
		params["printers_status"] = printersStatus
	}
	msg, err := c.signedCall(ctx, registerMethod, params)
	if err != nil {
		return nil, err
	}
	res := &RegisterResult{
		AgentID:              anyToStr(msg["agent_id"]),
		Status:               anyToStr(msg["status"]),
		MinAgentVersion:      anyToStr(msg["min_agent_version"]),
		PollIntervalSec:      anyToInt(msg["poll_interval_sec"]),
		InlineThresholdBytes: anyToInt(msg["pdf_inline_threshold_bytes"]),
		ScanPrintEnabled:     anyToBool(msg["scan_print_enabled"]),
		StationUser:          anyToStr(msg["station_user"]),
	}
	if res.AgentID != "" {
		c.AgentID = res.AgentID
	}
	return res, nil
}

// PollJob is one claimed job from agent_poll. Exactly one of PDFB64 /
// PDFURL is set (server inlines <= threshold, larger artifacts ride a
// short-TTL token URL that stays valid for Range resumes inside its TTL;
// integrity is verified via PDFSHA256).
type PollJob struct {
	JobID      string
	RefDoctype string
	RefDocname string
	DesignName string
	Copies     int // always 1: copies are merged server-side (plan 1)
	PDFSHA256  string
	FileName   string
	PDFBytes   int64
	PDFB64     string
	PDFURL     string
}

// PollResult is the agent_poll response.
type PollResult struct {
	Jobs            []PollJob
	PollIntervalSec int
	MinAgentVersion string
}

// Poll claims ready jobs for this station (server flips ready -> sent with
// an atomic guarded UPDATE, so concurrent agents never double-print).
func (c *Client) Poll(ctx context.Context, limit int) (*PollResult, error) {
	if limit <= 0 || limit > PollLimit {
		limit = PollLimit
	}
	msg, err := c.signedCall(ctx, pollMethod, map[string]string{
		"limit": strconv.Itoa(limit),
	})
	if err != nil {
		return nil, err
	}
	res := &PollResult{
		PollIntervalSec: anyToInt(msg["poll_interval_sec"]),
		MinAgentVersion: anyToStr(msg["min_agent_version"]),
	}
	rawJobs, _ := msg["jobs"].([]any)
	for _, raw := range rawJobs {
		m, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		res.Jobs = append(res.Jobs, PollJob{
			JobID:      anyToStr(m["job_id"]),
			RefDoctype: anyToStr(m["ref_doctype"]),
			RefDocname: anyToStr(m["ref_docname"]),
			DesignName: anyToStr(m["design_name"]),
			Copies:     anyToInt(m["copies"]),
			PDFSHA256:  anyToStr(m["pdf_sha256"]),
			FileName:   anyToStr(m["file_name"]),
			PDFBytes:   int64(anyToInt(m["pdf_bytes"])),
			PDFB64:     anyToStr(m["pdf_b64"]),
			PDFURL:     anyToStr(m["pdf_url"]),
		})
	}
	return res, nil
}

// Ack reports the print outcome. The server is idempotent for already-final
// jobs (already_final=true, status echoes the recorded terminal state).
func (c *Client) Ack(ctx context.Context, jobID string, exitCode int, detail string) (bool, string, error) {
	msg, err := c.signedCall(ctx, ackMethod, map[string]string{
		"job_id":    jobID,
		"exit_code": strconv.Itoa(exitCode),
		"detail":    truncate(detail, 500),
	})
	if err != nil {
		return false, "", err
	}
	return anyToBool(msg["already_final"]), anyToStr(msg["status"]), nil
}

// HeartbeatResult is the agent_heartbeat response.
type HeartbeatResult struct {
	PollIntervalSec  int
	MinAgentVersion  string
	ScanPrintEnabled bool
}

// Heartbeat keeps the Zprint Agent row alive (printers status + queue depth)
// and returns the current cadence / feature switch. v1 commands list is
// always empty and is surfaced only for logging.
func (c *Client) Heartbeat(ctx context.Context, printersStatus string, queueDepth int) (*HeartbeatResult, []string, error) {
	params := map[string]string{
		"agent_version": c.Version,
		"queue_depth":   strconv.Itoa(queueDepth),
	}
	if c.AgentID != "" {
		params["agent_id"] = c.AgentID
	}
	if printersStatus != "" {
		params["printers_status"] = printersStatus
	}
	msg, err := c.signedCall(ctx, heartbeatMethod, params)
	if err != nil {
		return nil, nil, err
	}
	res := &HeartbeatResult{
		PollIntervalSec:  anyToInt(msg["poll_interval_sec"]),
		MinAgentVersion:  anyToStr(msg["min_agent_version"]),
		ScanPrintEnabled: anyToBool(msg["scan_print_enabled"]),
	}
	var commands []string
	if raw, ok := msg["commands"].([]any); ok {
		for _, cm := range raw {
			commands = append(commands, fmt.Sprintf("%v", cm))
		}
	}
	return res, commands, nil
}

// ScanReceive posts one scan through the shared trunk. Business failures
// (no rule, gates, duplicates) arrive in-band in the returned envelope
// {job_id, status, server_time, dup, message}; only protocol problems
// (auth, validation) return an error.
func (c *Client) ScanReceive(ctx context.Context, stationID, scanCode, clientUUID string) (map[string]any, error) {
	return c.signedCall(ctx, scanMethod, map[string]string{
		"station_id":  stationID,
		"scan_code":   scanCode,
		"client_uuid": clientUUID,
	})
}

// Download fetches a large PDF from the one-time token URL (server streams
// private/files/scan_pdf with 200 / 206; a dead token answers 403 text).
func (c *Client) Download(ctx context.Context, rawURL string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusForbidden {
		return nil, ErrNotFound
	}
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusPartialContent {
		return nil, fmt.Errorf("download: HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
}

// DecodeB64PDF decodes an inline poll artifact, tolerating the line breaks
// some gateways inject into base64 bodies.
func DecodeB64PDF(s string) ([]byte, error) {
	clean := strings.Map(func(r rune) rune {
		if r == '\n' || r == '\r' {
			return -1
		}
		return r
	}, s)
	return base64.StdEncoding.DecodeString(clean)
}

// truncate caps detail at n bytes without slicing through a UTF-8 rune
// (the server re-caps at 140 for ack_code; the full text lands in the
// Zprint Scan Event message up to 500).
func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	cut := s[:n]
	for len(cut) > 0 && !utf8.ValidString(cut) {
		cut = cut[:len(cut)-1]
	}
	return cut
}
