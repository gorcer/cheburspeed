package main

import (
	"fmt"
	"strings"
)

// endpointResolver maps a configured peer endpoint ("host:port") to the
// literal amneziawg-go accepts. It is injected so the rendering itself
// stays testable without touching DNS.
type endpointResolver func(endpoint string) (string, error)

// buildUAPI renders the amneziawg-go configuration protocol payload for
// cfg. The keys map one-to-one onto the device's UAPI vocabulary:
//
//	private_key / listen_port        -> [Interface]
//	jc, jmin, jmax                   -> junk packet count / bounds
//	s1..s4                           -> initial / response / cookie /
//	                                    transport padding sizes
//	h1..h4                           -> the same four headers as "lo-hi"
//	                                    uint ranges
//	i1..i5                           -> obfuscated packet chains
//	public_key                       -> starts a new [Peer] block
//	preshared_key                    -> [Peer] psk
//	endpoint                         -> already-resolved "ip:port"
//	allowed_ip / replace_allowed_ips -> peer routing table
//	persistent_keepalive_interval    -> peer keepalive, in seconds
//
// Only optional keys that are actually present are emitted: amneziawg-go
// treats an empty value as a hard parse error, so writing "jc=" for an
// AWG 1.4-style config would make a perfectly valid tunnel fail.
func buildUAPI(cfg *tunnelConfig, resolve endpointResolver) (string, error) {
	var sb strings.Builder

	write := func(key, value string) {
		sb.WriteString(key)
		sb.WriteByte('=')
		sb.WriteString(value)
		sb.WriteByte('\n')
	}

	write("private_key", cfg.Interface.PrivateKey)
	write("listen_port", fmt.Sprintf("%d", cfg.Interface.ListenPort))

	for _, opt := range []struct {
		key   string
		value *int
	}{
		{"jc", cfg.Interface.JC},
		{"jmin", cfg.Interface.JMin},
		{"jmax", cfg.Interface.JMax},
		{"s1", cfg.Interface.S1},
		{"s2", cfg.Interface.S2},
		{"s3", cfg.Interface.S3},
		{"s4", cfg.Interface.S4},
	} {
		if opt.value != nil {
			write(opt.key, fmt.Sprintf("%d", *opt.value))
		}
	}
	for _, opt := range []struct {
		key   string
		value string
	}{
		{"h1", cfg.Interface.H1},
		{"h2", cfg.Interface.H2},
		{"h3", cfg.Interface.H3},
		{"h4", cfg.Interface.H4},
		{"i1", cfg.Interface.I1},
		{"i2", cfg.Interface.I2},
		{"i3", cfg.Interface.I3},
		{"i4", cfg.Interface.I4},
		{"i5", cfg.Interface.I5},
	} {
		if opt.value != "" {
			write(opt.key, opt.value)
		}
	}

	for _, peer := range cfg.Peers {
		if peer.PublicKey == "" {
			return "", fmt.Errorf("peer is missing public_key")
		}
		if peer.Endpoint == "" {
			return "", fmt.Errorf("peer %s is missing endpoint", peer.PublicKey)
		}
		endpoint, err := resolve(peer.Endpoint)
		if err != nil {
			return "", err
		}
		write("public_key", peer.PublicKey)
		if peer.PresharedKey != "" {
			write("preshared_key", peer.PresharedKey)
		}
		write("endpoint", endpoint)
		write("replace_allowed_ips", "true")
		for _, allowedIP := range peer.AllowedIPs {
			write("allowed_ip", allowedIP)
		}
		if peer.PersistentKeepalive != "" {
			write("persistent_keepalive_interval", peer.PersistentKeepalive)
		}
	}

	// A trailing blank line terminates the operation, which is what
	// commits the staged device/peer changes.
	sb.WriteByte('\n')
	return sb.String(), nil
}
