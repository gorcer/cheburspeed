module github.com/gorcer/cheburspeed/probe/awgproxy

// amneziawg-go/v3 is the only way to dial an AmneziaWG tunnel from
// userspace: xray-core and sing-box ship no AWG obfuscation parameters,
// and the kernel module would need NET_ADMIN plus a TUN device the probe
// does not have. Pinned exactly (plus go.sum) so an upstream release can
// never change the bytes on the wire of a production probe.
require (
	github.com/amnezia-vpn/amneziawg-go/v3 v3.0.1
	golang.org/x/net v0.44.0
	golang.org/x/sys v0.36.0
)

require (
	github.com/google/btree v1.1.3 // indirect
	golang.org/x/crypto v0.42.0 // indirect
	golang.org/x/time v0.9.0 // indirect
	golang.zx2c4.com/wintun v0.0.0-20230126152724-0fa3db229ce2 // indirect
	gvisor.dev/gvisor v0.0.0-20231202080848-1f7806d17489 // indirect
)

go 1.25.0
