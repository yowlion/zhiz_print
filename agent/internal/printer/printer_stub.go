//go:build !windows

// Non-windows stubs keep `go vet` / `go build` green on dev machines;
// none of these code paths run in production (the agent ships as a
// GOOS=windows static binary).

package printer

import "errors"

// Status mirrors the windows type so shared code compiles everywhere.
type Status struct{}

// Text is the stub twin of Status.Text.
func (s Status) Text() string { return "ok" }

// Healthy is the stub twin.
func (s Status) Healthy() bool { return true }

// Info mirrors the windows type.
type Info struct {
	Name       string
	Port       string
	Status     Status
	QueueDepth int
	Default    bool
	RawFlags   uint32
}

var errNotWindows = errors.New("winspool enumeration requires windows")

// List is the stub twin.
func List() ([]Info, error) { return nil, errNotWindows }

// Find is the stub twin.
func Find(name string) (*Info, error) { return nil, errNotWindows }

// PaperNames is the stub twin.
func PaperNames(printerName, port string) ([]string, error) { return nil, errNotWindows }

// HasPaperCode is the stub twin.
func HasPaperCode(printerName, port string, code uint16) (bool, error) {
	return false, errNotWindows
}
