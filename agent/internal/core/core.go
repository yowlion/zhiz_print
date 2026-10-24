// Package core is the ZhizPrintAgent runtime: it wires the ERP client,
// the winspool side-channel and the embedded scan page into one loop.
//
// Lifecycle (plan 2):
//
//	register -> serve scan page (+ kiosk shell) -> poll loop -> print -> ack
//	                                          \-> heartbeat (60s, printers)
//
// The poll loop claims jobs atomically server-side (ready -> sent guarded
// UPDATE), so two agents can never print the same job. Idle polls back off
// exponentially 1s..30s; a claimed job resets the cadence to the
// server-issued poll interval. Failed acks are retried before every poll -
// agent_ack is idempotent for already-terminal jobs.
package core

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/gdzhiz/zhizprintagent/internal/config"
	"github.com/gdzhiz/zhizprintagent/internal/erpclient"
	"github.com/gdzhiz/zhizprintagent/internal/kiosk"
	"github.com/gdzhiz/zhizprintagent/internal/printer"
	"github.com/gdzhiz/zhizprintagent/internal/scanpage"
	"github.com/gdzhiz/zhizprintagent/internal/version"
)

// Version is reported to the ERP and compared against min_agent_version.
const Version = version.Version

// JobView is the local job mirror the scan page polls (plan 5 four-state
// banner: accepted / printing / printed / failed).
type JobView struct {
	JobID      string `json:"job_id"`
	Code       string `json:"code,omitempty"`
	Status     string `json:"status"`
	FailReason string `json:"fail_reason,omitempty"`
}

type pendingAck struct {
	jobID  string
	code   int
	detail string
	at     time.Time
}

// Agent owns the runtime state.
type Agent struct {
	Cfg     *config.Config
	EP      *erpclient.Client
	pageURL string

	mu          sync.Mutex
	order       []string
	jobs        map[string]*JobView
	pendingAcks []pendingAck
	token       string
	scanEnabled bool
	erpOnline   bool
	versionLow  bool
}

// New assembles an Agent (does not start anything).
func New(cfg *config.Config, ep *erpclient.Client) *Agent {
	return &Agent{
		Cfg:  cfg,
		EP:   ep,
		jobs: make(map[string]*JobView),
	}
}

// Run blocks until ctx is cancelled: register, serve the scan page, launch
// the kiosk shell, then run the heartbeat and poll loops.
func (a *Agent) Run(ctx context.Context) error {
	a.token = scanpage.NewToken()
	a.mu.Lock()
	a.jobs = make(map[string]*JobView)
	a.mu.Unlock()

	// Register is best-effort at boot: the page must come up even when the
	// ERP is unreachable (operator sees the offline banner, plan 5).
	reg, err := a.EP.Register(ctx, a.printersStatusJSON(), a.queueDepth())
	if err != nil {
		log.Printf("[register] failed (will retry in the loop): %v", err)
		a.setERP(false, false)
	} else {
		a.applyRegister(reg)
	}

	addr, err := scanpage.Serve(a.Cfg.ListenAddr, a.token, a)
	if err != nil {
		return fmt.Errorf("scan page: %w", err)
	}
	log.Printf("[scanpage] serving %s", addr)
	a.pageURL = "http://" + addr.String() + "/s/" + a.token

	if a.Cfg.Kiosk {
		if err := kiosk.Launch(a.pageURL); err != nil {
			log.Printf("[kiosk] launch failed (page stays reachable at %s): %v", a.pageURL, err)
		}
	} else {
		log.Printf("[kiosk] disabled; open %s manually", a.pageURL)
	}

	hbDone := make(chan struct{})
	go func() {
		defer close(hbDone)
		a.heartbeatLoop(ctx)
	}()
	a.pollLoop(ctx)

	<-hbDone
	return nil
}

func (a *Agent) applyRegister(reg *erpclient.RegisterResult) {
	a.mu.Lock()
	a.scanEnabled = reg.ScanPrintEnabled
	a.erpOnline = true
	a.versionLow = erpclient.VersionBelow(Version, reg.MinAgentVersion)
	a.mu.Unlock()
	if reg.PollIntervalSec >= 1 {
		a.Cfg.PollIntervalSec = reg.PollIntervalSec
	}
	if reg.AgentID != "" && reg.AgentID != a.Cfg.AgentID {
		a.Cfg.AgentID = reg.AgentID
		if err := a.Cfg.Save(); err != nil {
			log.Printf("[config] persist agent_id failed: %v", err)
		}
	}
	if reg.MinAgentVersion != "" && a.versionLow {
		log.Printf("[register] WARNING: agent %s is below min_agent_version %s - upgrade the package",
			Version, reg.MinAgentVersion)
	}
	log.Printf("[register] ok station=%s agent_id=%s status=%s poll=%ds scan_print_enabled=%v",
		a.Cfg.StationID, reg.AgentID, reg.Status, a.Cfg.PollIntervalSec, reg.ScanPrintEnabled)
}

func (a *Agent) setERP(online, scanEnabled bool) {
	a.mu.Lock()
	a.erpOnline = online
	a.scanEnabled = scanEnabled
	a.mu.Unlock()
}

// ---------------------------------------------------------------------------
// Heartbeat (plan 2: 60s keepalive carrying printers status + queue depth)
// ---------------------------------------------------------------------------

func (a *Agent) heartbeatLoop(ctx context.Context) {
	interval := time.Duration(a.Cfg.HeartbeatSec) * time.Second
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			a.drainPendingAcks(ctx)
			res, commands, err := a.EP.Heartbeat(ctx, a.printersStatusJSON(), a.queueDepth())
			if err != nil {
				a.mu.Lock()
				a.erpOnline = false
				a.mu.Unlock()
				log.Printf("[heartbeat] failed: %v", err)
				continue
			}
			if res.PollIntervalSec >= 1 {
				a.Cfg.PollIntervalSec = res.PollIntervalSec
			}
			a.mu.Lock()
			a.erpOnline = true
			a.scanEnabled = res.ScanPrintEnabled
			a.versionLow = erpclient.VersionBelow(Version, res.MinAgentVersion)
			a.mu.Unlock()
			for _, c := range commands {
				log.Printf("[heartbeat] unsupported server command ignored: %s", c)
			}
		}
	}
}

func (a *Agent) muOnline() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.erpOnline
}

func (a *Agent) scanEnabledNow() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.scanEnabled
}

// ---------------------------------------------------------------------------
// Poll loop (claim -> download -> verify -> print -> ack)
// ---------------------------------------------------------------------------

func (a *Agent) pollLoop(ctx context.Context) {
	backoff := 0
	for {
		if ctx.Err() != nil {
			return
		}
		a.drainPendingAcks(ctx)
		res, err := a.EP.Poll(ctx, erpclient.PollLimit)
		if err != nil {
			a.mu.Lock()
			a.erpOnline = false
			a.mu.Unlock()
			sleep := idleSleep(backoff)
			backoff++
			log.Printf("[poll] failed, retrying in %s: %v", sleep, err)
			if !sleepCtx(ctx, sleep) {
				return
			}
			continue
		}
		a.mu.Lock()
		a.erpOnline = true
		a.mu.Unlock()
		if res.PollIntervalSec >= 1 {
			a.Cfg.PollIntervalSec = res.PollIntervalSec
		}
		if res.MinAgentVersion != "" && erpclient.VersionBelow(Version, res.MinAgentVersion) {
			a.mu.Lock()
			a.versionLow = true
			a.mu.Unlock()
		}
		if len(res.Jobs) == 0 {
			// Idle: exponential backoff 1s..30s (plan 2).
			sleep := idleSleep(backoff)
			backoff++
			if !sleepCtx(ctx, sleep) {
				return
			}
			continue
		}
		backoff = 0
		for _, job := range res.Jobs {
			if ctx.Err() != nil {
				return
			}
			a.processJob(ctx, job)
		}
		// Active cadence: the server-issued interval.
		if !sleepCtx(ctx, time.Duration(a.Cfg.PollIntervalSec)*time.Second) {
			return
		}
	}
}

func idleSleep(step int) time.Duration {
	d := time.Second << uint(step)
	if d > 30*time.Second || d <= 0 {
		return 30 * time.Second
	}
	return d
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func (a *Agent) processJob(ctx context.Context, job erpclient.PollJob) {
	if job.JobID == "" {
		return
	}
	log.Printf("[job %s] claimed doctype=%s doc=%s bytes=%d inline=%v",
		job.JobID, job.RefDoctype, job.RefDocname, job.PDFBytes, job.PDFB64 != "")
	a.setView(JobView{JobID: job.JobID, Code: a.codeFor(job.JobID), Status: "sent"})

	exitCode, detail := a.printJob(ctx, job)
	ackCode := printer.AckCode(exitCode)
	if detail == "" {
		detail = fmt.Sprintf("sumatra exit=%d", exitCode)
	}
	if err := a.ack(ctx, job.JobID, ackCode, detail); err != nil {
		log.Printf("[job %s] ack deferred: %v", job.JobID, err)
		a.queueAck(job.JobID, ackCode, detail)
	}
	if ackCode == 0 {
		a.setView(JobView{JobID: job.JobID, Code: a.codeFor(job.JobID),
			Status: "printed_queue_ok"})
		log.Printf("[job %s] printed_queue_ok", job.JobID)
	} else {
		a.setView(JobView{JobID: job.JobID, Code: a.codeFor(job.JobID),
			Status: "failed", FailReason: detail})
		log.Printf("[job %s] failed ack=%d: %s", job.JobID, ackCode, detail)
	}
}

// printJob downloads (or decodes) the artifact, verifies the digest and
// runs SumatraPDF. It returns the raw exit code plus the ack detail text.
func (a *Agent) printJob(ctx context.Context, job erpclient.PollJob) (int, string) {
	exeDir, err := os.Executable()
	if err != nil {
		exeDir = ""
	}
	exeDir = filepath.Dir(exeDir)
	sumatra, err := printer.FindSumatra(exeDir, a.Cfg.SumatraPath)
	if err != nil {
		return printer.SumatraExitCodeTimeout, err.Error()
	}

	// Printer pre-check (plan 11): a config printer missing from the
	// winspool enumeration is an immediate, well-explained failure - the
	// ERP maps ack code 2 to "printer not found on the print PC".
	osPrinter := a.Cfg.Printer
	if osPrinter != "" {
		if _, err := printer.Find(osPrinter); err != nil {
			return 2, err.Error()
		}
	}

	dir := filepath.Join(os.TempDir(), "zhizprint-agent")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return printer.SumatraExitCodeTimeout, "temp dir: " + err.Error()
	}
	pdfPath := filepath.Join(dir, sanitizeFileName(job.JobID)+".pdf")
	defer os.Remove(pdfPath)

	if job.PDFB64 != "" {
		data, err := erpclient.DecodeB64PDF(job.PDFB64)
		if err != nil {
			return 5, "inline pdf base64 invalid: " + err.Error()
		}
		if err := os.WriteFile(pdfPath, data, 0o600); err != nil {
			return printer.SumatraExitCodeTimeout, "write pdf: " + err.Error()
		}
	} else if job.PDFURL != "" {
		data, err := a.EP.Download(ctx, job.PDFURL)
		if err != nil {
			return printer.SumatraExitCodeTimeout, "download pdf: " + err.Error()
		}
		if err := os.WriteFile(pdfPath, data, 0o600); err != nil {
			return printer.SumatraExitCodeTimeout, "write pdf: " + err.Error()
		}
	} else {
		return 5, "job carries neither pdf_b64 nor pdf_url"
	}

	// Digest gate: a corrupted artifact must never reach paper silently.
	if job.PDFSHA256 != "" {
		got, err := fileSHA256(pdfPath)
		if err != nil {
			return printer.SumatraExitCodeTimeout, "sha256: " + err.Error()
		}
		if !strings.EqualFold(got, job.PDFSHA256) {
			return 5, "pdf sha256 mismatch (want " + job.PDFSHA256 + ", got " + got + ")"
		}
	}

	timeout := time.Duration(a.Cfg.PrintTimeoutSec) * time.Second
	code, output, printErr := printer.Print(sumatra, osPrinter, pdfPath, timeout)
	detail := fmt.Sprintf("sumatra exit=%d", code)
	if output != "" {
		detail += "; " + output
	}
	if printErr != nil && code != 0 {
		detail += "; " + printErr.Error()
	}
	return code, detail
}

func (a *Agent) ack(ctx context.Context, jobID string, code int, detail string) error {
	_, _, err := a.EP.Ack(ctx, jobID, code, detail)
	return err
}

func (a *Agent) queueAck(jobID string, code int, detail string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for i := range a.pendingAcks {
		if a.pendingAcks[i].jobID == jobID {
			a.pendingAcks[i].code = code
			a.pendingAcks[i].detail = detail
			a.pendingAcks[i].at = time.Now()
			return
		}
	}
	if len(a.pendingAcks) >= 100 {
		a.pendingAcks = a.pendingAcks[1:]
	}
	a.pendingAcks = append(a.pendingAcks, pendingAck{jobID: jobID, code: code, detail: detail, at: time.Now()})
}

func (a *Agent) drainPendingAcks(ctx context.Context) {
	a.mu.Lock()
	pending := a.pendingAcks
	a.pendingAcks = nil
	a.mu.Unlock()
	if len(pending) == 0 {
		return
	}
	var still []pendingAck
	for _, pa := range pending {
		if err := a.ack(ctx, pa.jobID, pa.code, pa.detail); err != nil {
			still = append(still, pa)
		} else {
			log.Printf("[ack] retried ok job=%s", pa.jobID)
		}
	}
	a.mu.Lock()
	a.pendingAcks = append(still, a.pendingAcks...)
	a.mu.Unlock()
}

// ---------------------------------------------------------------------------
// Scan page backend
// ---------------------------------------------------------------------------

// Boot implements scanpage.Backend.
func (a *Agent) Boot() map[string]any {
	a.mu.Lock()
	online := a.erpOnline
	enabled := a.scanEnabled
	low := a.versionLow
	a.mu.Unlock()
	return map[string]any{
		"station_id":         a.Cfg.StationID,
		"station_name":       a.Cfg.StationID,
		"wedge_suffix":       a.Cfg.WedgeSuffix,
		"scan_print_enabled": enabled,
		"erp_online":         online,
		"version_low":        low,
		"printer":            a.Cfg.Printer,
		"poll_interval_sec":  a.Cfg.PollIntervalSec,
		"agent_version":      Version,
		"page_title":         scanpage.PageTitle,
	}
}

// Submit implements scanpage.Backend: one scan through the shared trunk
// (zhiz_print.api.scan_print.scan_receive, station HMAC path). The page
// never touches the API secret.
func (a *Agent) Submit(code string) map[string]any {
	resp := map[string]any{
		"job_id": "", "status": "rejected", "dup": false, "message": "",
	}
	if strings.TrimSpace(code) == "" {
		resp["error"] = "empty scan"
		return resp
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	envelope, err := a.EP.ScanReceive(ctx, a.Cfg.StationID, code, newUUID())
	if err != nil {
		// Protocol-level failure (auth, gates raised as exceptions, network).
		resp["error"] = err.Error()
		a.mu.Lock()
		a.erpOnline = !isTransportErr(err)
		a.mu.Unlock()
		return resp
	}
	a.mu.Lock()
	a.erpOnline = true
	a.mu.Unlock()
	for _, key := range []string{"job_id", "status", "dup", "message", "server_time"} {
		if v, ok := envelope[key]; ok {
			resp[key] = v
		}
	}
	if jobID, ok := envelope["job_id"].(string); ok && jobID != "" {
		status, _ := envelope["status"].(string)
		a.setView(JobView{JobID: jobID, Code: code, Status: status})
	}
	return resp
}

// Status implements scanpage.Backend: local mirror only - the agent knows
// the outcome of every job it prints and get_job_status is session-only on
// the ERP side.
func (a *Agent) Status(jobIDs []string) map[string]any {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make([]JobView, 0, len(jobIDs))
	seen := make(map[string]bool, len(jobIDs))
	// Newest first for the page history.
	for i := len(a.order) - 1; i >= 0; i-- {
		id := a.order[i]
		if v, ok := a.jobs[id]; ok && len(out) < 50 {
			out = append(out, *v)
			seen[id] = true
		}
	}
	for _, id := range jobIDs {
		if !seen[id] {
			if v, ok := a.jobs[id]; ok {
				out = append(out, *v)
			}
		}
	}
	return map[string]any{"jobs": out}
}

// Focused implements scanpage.Backend: the kiosk shell owns the foreground
// when its window title carries the page title.
func (a *Agent) Focused() bool {
	title := kiosk.ForegroundTitle()
	return strings.Contains(title, scanpage.PageTitle)
}

func (a *Agent) setView(v JobView) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.jobs == nil {
		a.jobs = make(map[string]*JobView)
	}
	if _, ok := a.jobs[v.JobID]; !ok {
		a.order = append(a.order, v.JobID)
		if len(a.order) > 50 {
			old := a.order[0]
			a.order = a.order[1:]
			delete(a.jobs, old)
		}
	}
	existing := a.jobs[v.JobID]
	if v.Code == "" && existing != nil {
		v.Code = existing.Code
	}
	clone := v
	a.jobs[v.JobID] = &clone
}

// codeFor recovers the scanned code of a claimed job (desk reprints and
// PDA-submitted jobs were never seen by Submit).
func (a *Agent) codeFor(jobID string) string {
	a.mu.Lock()
	defer a.mu.Unlock()
	if v, ok := a.jobs[jobID]; ok {
		return v.Code
	}
	return ""
}

// ---------------------------------------------------------------------------
// Winspool snapshots for the heartbeat
// ---------------------------------------------------------------------------

type heartbeatPrinter struct {
	Name   string `json:"name"`
	Port   string `json:"port"`
	Status string `json:"status"`
	Depth  int    `json:"depth"`
	Dflt   bool   `json:"default,omitempty"`
}

func (a *Agent) printersStatusJSON() string {
	all, err := printer.List()
	if err != nil {
		log.Printf("[printers] enumerate failed: %v", err)
		return ""
	}
	if len(all) == 0 {
		return ""
	}
	sort.Slice(all, func(i, j int) bool { return all[i].Name < all[j].Name })
	payload := struct {
		Printers []heartbeatPrinter `json:"printers"`
		Depth    int                `json:"depth"`
	}{Printers: make([]heartbeatPrinter, 0, len(all))}
	for _, p := range all {
		payload.Printers = append(payload.Printers, heartbeatPrinter{
			Name:   p.Name,
			Port:   p.Port,
			Status: p.Status.Text(),
			Depth:  p.QueueDepth,
			Dflt:   p.Default,
		})
		payload.Depth += p.QueueDepth
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return ""
	}
	return string(data)
}

func (a *Agent) queueDepth() int {
	all, err := printer.List()
	if err != nil {
		return 0
	}
	depth := 0
	for _, p := range all {
		depth += p.QueueDepth
	}
	return depth
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

func newUUID() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return fmt.Sprintf("u-%d", time.Now().UnixNano())
	}
	// RFC 4122 v4 shape (client_uuid only needs uniqueness).
	buf[6] = (buf[6] & 0x0f) | 0x40
	buf[8] = (buf[8] & 0x3f) | 0x80
	h := hex.EncodeToString(buf)
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:32]
}

func sanitizeFileName(name string) string {
	out := strings.Map(func(r rune) rune {
		switch r {
		case '/', '\\', ':', '*', '?', '"', '<', '>', '|', ' ':
			return '_'
		}
		if r < 0x20 {
			return '_'
		}
		return r
	}, name)
	if out == "" {
		return "job"
	}
	return out
}

// isTransportErr reports whether err came from the HTTP layer (as opposed
// to an ERP-side rejection like EntitlementDenied, which must keep the
// online flag green).
func isTransportErr(err error) bool {
	var urlErr *url.Error
	if errors.As(err, &urlErr) {
		return true
	}
	return errors.Is(err, http.ErrServerClosed) ||
		errors.Is(err, context.DeadlineExceeded) ||
		errors.Is(err, io.EOF)
}

// fileSHA256 hex-digests a file (artifact integrity gate before printing).
func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
