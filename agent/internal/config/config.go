// Package config loads and persists the ZhizPrintAgent configuration file
// (config.json under %LOCALAPPDATA%\ZhizPrintAgent). The measured server
// clock offset is persisted here too (plan 6-2): every authenticated
// response refreshes it and only real changes hit the disk.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Defaults, mirrored from Zprint Setting (api/agent.py) where applicable.
const (
	DefaultListenAddr   = "127.0.0.1:17980"
	DefaultPollInterval = 3
	DefaultHeartbeatSec = 60
	DefaultPrintTimeout = 120
	DefaultPaper        = "A4"
	DefaultWedgeSuffix  = "enter"
)

// ErrNotFound is returned by Load when the first-run wizard must run.
var ErrNotFound = errors.New("config.json not found (run the first-start wizard or `agent.exe doctor`)")

// Config is the on-disk shape of config.json. The API secret is stored in
// plain text on purpose for v1: the file never leaves the workstation
// profile directory (user-private NTFS ACL) and the README documents the
// trade-off together with the DPAPI upgrade path.
type Config struct {
	StationID  string `json:"station_id"`
	APIKey     string `json:"api_key"`
	APISecret  string `json:"api_secret"`
	ERPBaseURL string `json:"erp_base_url"`

	// Printer is the Windows (OS) printer name override; when empty the
	// agent prints to the Windows default printer via SumatraPDF.
	Printer string `json:"printer"`
	// Paper is a doctor-only check (paper code must exist in the driver's
	// capability list, plan 11: domestic driver paper traps).
	Paper string `json:"paper"`
	// AgentID is optional; the server assigns "agent-<station_id>".
	AgentID string `json:"agent_id"`
	// HTTPProxy overrides the proxy for ERP traffic; empty = environment.
	HTTPProxy string `json:"http_proxy"`
	// WedgeSuffix mirrors Zprint Station.wedge_suffix for the local page
	// (enter | tab | none).
	WedgeSuffix string `json:"wedge_suffix"`
	// ListenAddr is the loopback address of the embedded scan page.
	ListenAddr string `json:"listen_addr"`
	// Kiosk launches msedge --kiosk on the scan page at start.
	Kiosk bool `json:"kiosk"`
	// SumatraPath optionally points at SumatraPDF.exe; default is next to
	// agent.exe (bundled layout, plan 2).
	SumatraPath string `json:"sumatra_path"`

	// ClockOffsetSec = server_time - local_time; refreshed from every
	// authenticated response and persisted on change.
	ClockOffsetSec int64 `json:"clock_offset_sec"`
	// PollIntervalSec is the server-issued cadence (agent_register /
	// agent_poll / agent_heartbeat responses).
	PollIntervalSec int `json:"poll_interval_sec"`
	// HeartbeatSec is the local keepalive cadence (plan 2: 60s).
	HeartbeatSec int `json:"heartbeat_interval_sec"`
	// PrintTimeoutSec bounds one SumatraPDF run.
	PrintTimeoutSec int `json:"print_timeout_sec"`
}

// Dir returns %LOCALAPPDATA%\ZhizPrintAgent.
func Dir() (string, error) {
	base := os.Getenv("LOCALAPPDATA")
	if strings.TrimSpace(base) == "" {
		return "", errors.New("LOCALAPPDATA is not set; expected a Windows user profile")
	}
	return filepath.Join(base, "ZhizPrintAgent"), nil
}

// Path returns the config.json absolute path.
func Path() (string, error) {
	dir, err := Dir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "config.json"), nil
}

// Load reads and normalises config.json. Returns ErrNotFound when absent.
func Load() (*Config, error) {
	p, err := Path()
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil, fmt.Errorf("config.json: %w", err)
	}
	cfg.normalize()
	return &cfg, nil
}

// Save writes config.json atomically (tmp file + rename) so a crash never
// leaves a truncated credential file behind.
func (c *Config) Save() error {
	dir, err := Dir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	c.normalize()
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	tmp := filepath.Join(dir, "config.json.tmp")
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(dir, "config.json"))
}

// Validate enforces the minimum the signed protocol needs.
func (c *Config) Validate() error {
	var missing []string
	if strings.TrimSpace(c.StationID) == "" {
		missing = append(missing, "station_id")
	}
	if strings.TrimSpace(c.APIKey) == "" {
		missing = append(missing, "api_key")
	}
	if strings.TrimSpace(c.APISecret) == "" {
		missing = append(missing, "api_secret")
	}
	if strings.TrimSpace(c.ERPBaseURL) == "" {
		missing = append(missing, "erp_base_url")
	}
	if len(missing) > 0 {
		return fmt.Errorf("config.json missing: %s", strings.Join(missing, ", "))
	}
	if !strings.HasPrefix(c.ERPBaseURL, "http://") && !strings.HasPrefix(c.ERPBaseURL, "https://") {
		return errors.New("erp_base_url must start with http:// or https://")
	}
	switch c.WedgeSuffix {
	case "enter", "tab", "none":
	default:
		return fmt.Errorf("wedge_suffix must be enter, tab or none (got %q)", c.WedgeSuffix)
	}
	return nil
}

func (c *Config) normalize() {
	c.StationID = strings.TrimSpace(c.StationID)
	c.APIKey = strings.TrimSpace(c.APIKey)
	c.APISecret = strings.TrimSpace(c.APISecret)
	c.ERPBaseURL = strings.TrimRight(strings.TrimSpace(c.ERPBaseURL), "/")
	c.Printer = strings.TrimSpace(c.Printer)
	c.Paper = strings.ToUpper(strings.TrimSpace(c.Paper))
	c.AgentID = strings.TrimSpace(c.AgentID)
	c.HTTPProxy = strings.TrimSpace(c.HTTPProxy)
	c.SumatraPath = strings.TrimSpace(c.SumatraPath)
	c.WedgeSuffix = strings.ToLower(strings.TrimSpace(c.WedgeSuffix))
	if c.WedgeSuffix == "" {
		c.WedgeSuffix = DefaultWedgeSuffix
	}
	if strings.TrimSpace(c.ListenAddr) == "" {
		c.ListenAddr = DefaultListenAddr
	}
	if c.Paper == "" {
		c.Paper = DefaultPaper
	}
	if c.PollIntervalSec < 1 {
		c.PollIntervalSec = DefaultPollInterval
	}
	if c.HeartbeatSec < 15 {
		c.HeartbeatSec = DefaultHeartbeatSec
	}
	if c.PrintTimeoutSec < 30 {
		c.PrintTimeoutSec = DefaultPrintTimeout
	}
}
