// Package printer bundles the two halves of Channel A printing:
//
//   - print.go (platform neutral): locate the pinned SumatraPDF.exe and run
//     it with the silent-print argument set, then map the raw exit code onto
//     the ERP ack scale.
//   - printer_windows.go: winspool side-channel (PRINTER_INFO_2/6 status,
//     queue depth, paper capability) for the heartbeat and doctor checks.
package printer

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// SumatraExitCodeTimeout is reported when the print run exceeds the
// configured timeout and the process is killed (never a real SumatraPDF
// exit code, negative on purpose: ProcessState.ExitCode() of a killed
// process is also negative).
const SumatraExitCodeTimeout = -1

// FindSumatra resolves SumatraPDF.exe: an explicit config path wins, then
// the bundled layout next to agent.exe, then the install directory.
// Missing SumatraPDF is a hard setup error - the agent never downloads
// binaries on its own (plan 2: 3.6.1 pinned, 3.5.0-3.5.2 have public RCE).
func FindSumatra(exeDir, explicit string) (string, error) {
	candidates := []string{}
	if explicit != "" {
		candidates = append(candidates, explicit)
	}
	if exeDir != "" {
		candidates = append(candidates, filepath.Join(exeDir, "SumatraPDF.exe"))
	}
	if local := os.Getenv("LOCALAPPDATA"); local != "" {
		candidates = append(candidates,
			filepath.Join(local, "ZhizPrintAgent", "SumatraPDF.exe"))
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && !st.IsDir() {
			return c, nil
		}
	}
	return "", errors.New(
		"SumatraPDF.exe not found next to agent.exe (bundle SumatraPDF 3.6.1 x64 with the agent package)")
}

// Print runs one silent print:
//
//	SumatraPDF.exe -print-to "<printer>" -print-settings "noscale" -silent -exit-when-done <pdf>
//
// An empty osPrinter means "Windows default printer" (the -print-to flag is
// omitted). Returns the raw SumatraPDF exit code and a combined-output tail
// for the ack detail. A timeout kills the process and yields exit code -1.
func Print(sumatraExe, osPrinter, pdfPath string, timeout time.Duration) (exitCode int, output string, err error) {
	if _, err := os.Stat(pdfPath); err != nil {
		return SumatraExitCodeTimeout, "", fmt.Errorf("pdf missing: %w", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	args := []string{}
	if strings.TrimSpace(osPrinter) != "" {
		args = append(args, "-print-to", osPrinter)
	}
	args = append(args,
		"-print-settings", "noscale",
		"-silent",
		"-exit-when-done",
		pdfPath,
	)
	cmd := exec.CommandContext(ctx, sumatraExe, args...)
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	cmd.Dir = filepath.Dir(sumatraExe) // Sumatra writes settings next to the exe in portable layouts

	runErr := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return SumatraExitCodeTimeout, tail(buf.String()), errors.New("print timed out")
	}
	code := 1
	if cmd.ProcessState != nil {
		code = cmd.ProcessState.ExitCode()
		if code < 0 { // killed by signal
			code = SumatraExitCodeTimeout
		}
	}
	return code, tail(buf.String()), runErr
}

// AckCode relays the raw print exit code onto the ERP ack scale
// (zhiz_print/zhiz_print/print_channels/agent.py apply_ack):
//
//	0 -> printed_queue_ok
//	2 -> printer not found on the print PC
//	3 -> printer offline or in error state
//	4 -> print spooler rejected the job
//	5 -> rendered file could not be opened by the printer engine
//	6 -> unknown agent error
//
// SumatraPDF 3.6.1 `-silent -exit-when-done` exit codes (best-effort; the
// vendor CLI publishes no stable contract, so the raw code always travels
// in the ack detail):
//
//	0 = print data handed to the Windows spooler
//	1 = generic failure (bad args, unreadable file, engine init)
//	2 = driver/spooler level failure
//	3 = printing canceled
//	4 = printer could not be opened
//	5 = no printable content (empty / corrupt document)
//	6 = out of memory
//
// 2-6 pass through unchanged; 1 and anything unknown (negative = killed /
// crashed) map to 6. A missing printer is caught before Sumatra runs (the
// caller acks 2 directly with the winspool enumeration as evidence).
func AckCode(raw int) int {
	switch {
	case raw == 0:
		return 0
	case raw >= 2 && raw <= 6:
		return raw
	default:
		return 6
	}
}

func tail(s string) string {
	s = strings.TrimSpace(s)
	if len(s) <= 400 {
		return s
	}
	return s[len(s)-400:]
}
