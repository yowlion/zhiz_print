module github.com/gdzhiz/zhizprintagent

go 1.26.0

// Single third-party dependency: golang.org/x/sys (Windows syscalls only).
// go.sum is produced at build time on a networked machine:
//
//	go mod tidy
//
// Offline rebuilds from a primed module cache work with GOPROXY=off.
require golang.org/x/sys v0.49.0
