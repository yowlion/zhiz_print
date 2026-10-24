//go:build !windows

// Non-windows stub so dev machines type-check the whole module.

package kiosk

import "errors"

// Launch is the stub twin (never runs outside windows).
func Launch(pageURL string) error {
	return errors.New("kiosk shell requires windows")
}

// ForegroundTitle is the stub twin.
func ForegroundTitle() string { return "" }
