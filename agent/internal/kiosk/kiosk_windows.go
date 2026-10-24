//go:build windows

// Package kiosk launches the Edge kiosk shell around the embedded scan page
// (plan 2/5: factory PC + USB HID gun; the kiosk shell keeps focus on the
// work window so a WeChat popup cannot swallow scans) and reports the
// foreground window title for the focus watchdog.
package kiosk

import (
	"fmt"
	"os"
	"os/exec"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	user32               = windows.NewLazySystemDLL("user32.dll")
	procGetForegroundWnd = user32.NewProc("GetForegroundWindow")
	procGetWindowTextW   = user32.NewProc("GetWindowTextW")
)

// edgeCandidates covers the standard per-machine and per-user installs of
// Microsoft Edge (msedge is not reliably on PATH).
func edgeCandidates() []string {
	out := []string{}
	add := func(parts ...string) {
		if base := os.Getenv(parts[0]); base != "" {
			out = append(out, joinPath(base, parts[1:]...))
		}
	}
	add("ProgramFiles(x86)", "Microsoft", "Edge", "Application", "msedge.exe")
	add("ProgramFiles", "Microsoft", "Edge", "Application", "msedge.exe")
	add("LocalAppData", "Microsoft", "Edge", "Application", "msedge.exe")
	return out
}

func joinPath(base string, parts ...string) string {
	p := base
	for _, part := range parts {
		p = p + string(os.PathSeparator) + part
	}
	return p
}

// Launch starts msedge in fullscreen kiosk mode pointed at pageURL. The
// process is deliberately not waited on; Edge keeps running when the agent
// restarts and simply reloads the (tokenised) page URL.
func Launch(pageURL string) error {
	exe := ""
	for _, c := range edgeCandidates() {
		if st, err := os.Stat(c); err == nil && !st.IsDir() {
			exe = c
			break
		}
	}
	if exe == "" {
		return fmt.Errorf("msedge.exe not found (install Microsoft Edge or disable the kiosk shell in config.json)")
	}
	cmd := exec.Command(exe,
		"--kiosk", pageURL,
		"--edge-kiosk-type=fullscreen",
		"--no-first-run",
		"--new-window",
	)
	return cmd.Start()
}

// ForegroundTitle returns the title of the current foreground window
// (empty when the desktop has no foreground window, e.g. locked session).
func ForegroundTitle() string {
	hwnd, _, _ := procGetForegroundWnd.Call()
	if hwnd == 0 {
		return ""
	}
	buf := make([]uint16, 512)
	n, _, _ := procGetWindowTextW.Call(hwnd,
		uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	if n == 0 {
		return ""
	}
	return windows.UTF16ToString(buf[:n])
}
