//go:build windows

package printer

import (
	"fmt"
	"unsafe"

	"golang.org/x/sys/windows"
)

// winspool.drv side-channel (plan 2): the agent never spools by itself -
// SumatraPDF does - but the heartbeat reports PRINTER_INFO_6 style status
// bits and queue depth, and doctor validates the configured printer and
// paper against the driver's capability list (plan 11: domestic driver
// paper traps).

var (
	winspool          = windows.NewLazySystemDLL("winspool.drv")
	procEnumPrintersW = winspool.NewProc("EnumPrintersW")
	procOpenPrinterW  = winspool.NewProc("OpenPrinterW")
	procClosePrinter  = winspool.NewProc("ClosePrinter")
	procDeviceCapsW   = winspool.NewProc("DeviceCapabilitiesW")
)

const (
	printerEnumLocal        = 0x00000002
	printerEnumConnections  = 0x00000004
	printerAttributeDefault = 0x00000004
	printerInfoLevel2       = 2
	printerInfoLevel6       = 6

	dcPapers     = 1 // DeviceCapabilities: paper code list
	dcPaperNames = 2 // DeviceCapabilities: paper name list
	paperNameLen = 64
)

// PRINTER_STATUS_* bits (PRINTER_INFO_2.Status / PRINTER_INFO_6.Status,
// wingdi.h). NOTE: PRINTER_STATUS_PAPER_OUT is 0x10 and PAPER_JAM is 0x8 -
// any other bit assignment (e.g. 0x400 / 0x800) collides with PRINTING /
// OUTPUT_BIN_FULL and misreports.
type Status struct {
	Paused           bool // 0x00000001
	Error            bool // 0x00000002
	PaperJam         bool // 0x00000008
	PaperOut         bool // 0x00000010
	PaperProblem     bool // 0x00000040
	Offline          bool // 0x00000080
	NoToner          bool // 0x00040000
	TonerLow         bool // 0x00020000
	UserIntervention bool // 0x00100000
	DoorOpen         bool // 0x00400000
}

// Info is one local printer as seen by winspool.
type Info struct {
	Name       string
	Port       string
	Status     Status
	QueueDepth int
	Default    bool
	RawFlags   uint32
}

// prInfo2 mirrors PRINTER_INFO_2W on win64 (pointers first, then DWORDs).
type prInfo2 struct {
	PServerName     *uint16
	PPrinterName    *uint16
	PShareName      *uint16
	PPortName       *uint16
	PDriverName     *uint16
	PComment        *uint16
	PLocation       *uint16
	PDevMode        uintptr
	PSepFile        *uint16
	PPrintProcessor *uint16
	PDatatype       *uint16
	PParameters     *uint16
	PSecDescriptor  uintptr
	Attributes      uint32
	Priority        uint32
	DefaultPriority uint32
	StartTime       uint32
	UntilTime       uint32
	Status          uint32
	CJobs           uint32
	AveragePPM      uint32
}

func statusFromFlags(flags uint32) Status {
	return Status{
		Paused:           flags&0x1 != 0,
		Error:            flags&0x2 != 0,
		PaperJam:         flags&0x8 != 0,
		PaperOut:         flags&0x10 != 0,
		PaperProblem:     flags&0x40 != 0,
		Offline:          flags&0x80 != 0,
		TonerLow:         flags&0x20000 != 0,
		NoToner:          flags&0x40000 != 0,
		UserIntervention: flags&0x100000 != 0,
		DoorOpen:         flags&0x400000 != 0,
	}
}

// StatusText renders the status flags as a compact pipe-joined token list
// for the heartbeat payload ("ok" when nothing is flagged).
func (s Status) Text() string {
	if s == (Status{}) {
		return "ok"
	}
	out := ""
	add := func(name string, on bool) {
		if on {
			if out != "" {
				out += "|"
			}
			out += name
		}
	}
	add("paused", s.Paused)
	add("error", s.Error)
	add("paper_jam", s.PaperJam)
	add("paper_out", s.PaperOut)
	add("paper_problem", s.PaperProblem)
	add("offline", s.Offline)
	add("toner_low", s.TonerLow)
	add("no_toner", s.NoToner)
	add("intervention", s.UserIntervention)
	add("door_open", s.DoorOpen)
	return out
}

// Healthy reports whether the flags block a spooler submission outright.
// Only hard offline / error-class bits count; paper_out / jam do NOT -
// the plan's receipt ceiling is the spooler commit, and most spoolers hold
// jobs until paper returns (plan 11).
func (s Status) Healthy() bool {
	return !s.Error && !s.Offline && !s.UserIntervention
}

// List enumerates local + mapped-network printers (PRINTER_INFO_2).
func List() ([]Info, error) {
	flags := uintptr(printerEnumLocal | printerEnumConnections)
	var needed, returned uint32
	ret, _, err := procEnumPrintersW.Call(flags, 0, printerInfoLevel2, 0, 0,
		uintptr(unsafe.Pointer(&needed)), uintptr(unsafe.Pointer(&returned)))
	if ret == 0 && needed == 0 {
		return nil, fmt.Errorf("EnumPrintersW: %v", err)
	}
	buf := make([]byte, needed)
	ret, _, err = procEnumPrintersW.Call(flags, 0, printerInfoLevel2,
		uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)),
		uintptr(unsafe.Pointer(&needed)), uintptr(unsafe.Pointer(&returned)))
	if ret == 0 {
		return nil, fmt.Errorf("EnumPrintersW: %v", err)
	}
	infos := unsafe.Slice((*prInfo2)(unsafe.Pointer(&buf[0])), returned)
	out := make([]Info, 0, returned)
	for i := range infos {
		p := &infos[i]
		name := ""
		if p.PPrinterName != nil {
			name = windows.UTF16PtrToString(p.PPrinterName)
		}
		if name == "" {
			continue
		}
		port := ""
		if p.PPortName != nil {
			port = windows.UTF16PtrToString(p.PPortName)
		}
		out = append(out, Info{
			Name:       name,
			Port:       port,
			Status:     statusFromFlags(p.Status),
			QueueDepth: int(p.CJobs),
			Default:    p.Attributes&printerAttributeDefault != 0,
			RawFlags:   p.Status,
		})
	}
	return out, nil
}

// Find returns the printer with the exact OS name.
func Find(name string) (*Info, error) {
	all, err := List()
	if err != nil {
		return nil, err
	}
	for i := range all {
		if all[i].Name == name {
			return &all[i], nil
		}
	}
	return nil, fmt.Errorf("printer %q not found on this PC", name)
}

// openPrinter resolves a printer handle for capability queries.
func openPrinter(name string) (windows.Handle, error) {
	ptr, err := windows.UTF16PtrFromString(name)
	if err != nil {
		return 0, err
	}
	var h windows.Handle
	ret, _, callErr := procOpenPrinterW.Call(uintptr(unsafe.Pointer(ptr)),
		uintptr(unsafe.Pointer(&h)), 0)
	if ret == 0 {
		return 0, fmt.Errorf("OpenPrinterW(%s): %v", name, callErr)
	}
	return h, nil
}

// PaperNames returns the driver's paper name list (DeviceCapabilities
// DC_PAPERNAMES). Empty list or -1 return means the driver exposes no
// capability list (common on GDI hosts) - callers treat that as "unknown",
// not as a failure.
func PaperNames(printerName, port string) ([]string, error) {
	dev, err := windows.UTF16PtrFromString(printerName)
	if err != nil {
		return nil, err
	}
	portPtr, err := windows.UTF16PtrFromString(port)
	if err != nil {
		return nil, err
	}
	count, _, _ := procDeviceCapsW.Call(uintptr(unsafe.Pointer(dev)),
		uintptr(unsafe.Pointer(portPtr)), dcPaperNames, 0, 0)
	if int32(count) <= 0 {
		return nil, nil
	}
	buf := make([]uint16, int(count)*paperNameLen)
	ret, _, _ := procDeviceCapsW.Call(uintptr(unsafe.Pointer(dev)),
		uintptr(unsafe.Pointer(portPtr)), dcPaperNames,
		uintptr(unsafe.Pointer(&buf[0])), 0)
	if int32(ret) <= 0 {
		return nil, nil
	}
	names := make([]string, 0, int(ret))
	for i := 0; i < int(ret); i++ {
		chunk := buf[i*paperNameLen : (i+1)*paperNameLen]
		names = append(names, utf16ToString(chunk))
	}
	return names, nil
}

// HasPaperCode reports whether the driver's DC_PAPERS list contains code
// (e.g. 9 = DMPAPER_A4). ok=false with err=nil means "not in list" or
// "driver exposes no list" - doctor words those two differently.
func HasPaperCode(printerName, port string, code uint16) (bool, error) {
	dev, err := windows.UTF16PtrFromString(printerName)
	if err != nil {
		return false, err
	}
	portPtr, err := windows.UTF16PtrFromString(port)
	if err != nil {
		return false, err
	}
	count, _, _ := procDeviceCapsW.Call(uintptr(unsafe.Pointer(dev)),
		uintptr(unsafe.Pointer(portPtr)), dcPapers, 0, 0)
	if int32(count) <= 0 {
		return false, nil
	}
	buf := make([]uint16, int(count))
	ret, _, _ := procDeviceCapsW.Call(uintptr(unsafe.Pointer(dev)),
		uintptr(unsafe.Pointer(portPtr)), dcPapers,
		uintptr(unsafe.Pointer(&buf[0])), 0)
	if int32(ret) <= 0 {
		return false, nil
	}
	for _, c := range buf[:int(ret)] {
		if c == code {
			return true, nil
		}
	}
	return false, nil
}

func utf16ToString(s []uint16) string {
	for i, c := range s {
		if c == 0 {
			return windows.UTF16ToString(s[:i])
		}
	}
	return windows.UTF16ToString(s)
}
