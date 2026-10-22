//go:build !linux

package main

import (
	"fmt"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
)

// The probe image is linux-only, but `go vet ./...` and cross-platform
// builds should still see a compilable tree. Interface pinning is a
// Linux socket option, so on every other platform -bind-device is
// rejected up front instead of silently measuring through the wrong
// NIC.
func newBind(iface string) (conn.Bind, error) {
	if iface != "" {
		return nil, fmt.Errorf("bind-device %q is only supported on linux", iface)
	}
	return conn.NewDefaultBind(), nil
}
