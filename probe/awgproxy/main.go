// Command awgsocks exposes an AmneziaWG tunnel as a local SOCKS5 proxy.
//
// The CheburSpeed probe measures download throughput through a SOCKS5
// proxy. For vless links that proxy is xray-core's SOCKS5 inbound; for
// AmneziaWG there is no xray support at all, so this helper provides
// the same contract using amneziawg-go (gVisor netstack + the AWG
// obfuscation parameters) instead of a kernel TUN device.
//
// Userspace is not a stylistic choice: probes run unprivileged
// (--network=host, uid 1000, no /dev/net/tun, no NET_ADMIN) and share
// the host network namespace, so a kernel interface is not an option.
// Everything below runs in-process and needs no capabilities.
//
// Usage:
//
//	awgsocks -config /tmp/awg-42.json -socks 127.0.0.1:1080 [-bind-device eth0]
//
// The config file is the JSON produced by xspeed_probe.awg and holds the
// tunnel's private key, so the probe writes it with mode 0600.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/netip"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/amnezia-vpn/amneziawg-go/v3/device"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun/netstack"
)

// defaultMTU is used when the config omits MTU. 1420 is the value
// AmneziaVPN ships and leaves room for the AWG data header on top of a
// 1500-byte Ethernet frame.
const defaultMTU = 1420

type ifaceConfig struct {
	PrivateKey string   `json:"private_key"`
	Addresses  []string `json:"addresses"`
	DNS        []string `json:"dns"`
	MTU        int      `json:"mtu"`
	ListenPort int      `json:"listen_port"`
	// AWG obfuscation knobs. jc/jmin/jmax/s1..s4 are counters, h1..h4
	// are "lo-hi" ranges and i1..i5 are packet-chain specs; they are
	// forwarded verbatim because every value changes the bytes on the
	// wire and must match the tunnel server exactly.
	JC   *int   `json:"jc"`
	JMin *int   `json:"jmin"`
	JMax *int   `json:"jmax"`
	S1   *int   `json:"s1"`
	S2   *int   `json:"s2"`
	S3   *int   `json:"s3"`
	S4   *int   `json:"s4"`
	H1   string `json:"h1"`
	H2   string `json:"h2"`
	H3   string `json:"h3"`
	H4   string `json:"h4"`
	I1   string `json:"i1"`
	I2   string `json:"i2"`
	I3   string `json:"i3"`
	I4   string `json:"i4"`
	I5   string `json:"i5"`
}

type peerConfig struct {
	PublicKey           string   `json:"public_key"`
	Endpoint            string   `json:"endpoint"`
	PresharedKey        string   `json:"preshared_key"`
	AllowedIPs          []string `json:"allowed_ips"`
	PersistentKeepalive string   `json:"persistent_keepalive"`
}

type tunnelConfig struct {
	Interface ifaceConfig  `json:"interface"`
	Peers     []peerConfig `json:"peers"`
}

func main() {
	var (
		configPath     = flag.String("config", "", "path to the AmneziaWG JSON config")
		socksAddr      = flag.String("socks", "127.0.0.1:1080", "address the SOCKS5 proxy listens on")
		bindDevice     = flag.String("bind-device", "", "pin the tunnel's outgoing UDP to this Linux interface")
		connectTimeout = flag.Duration("connect-timeout", 30*time.Second, "per-connection SOCKS5 dial timeout")
	)
	flag.Parse()

	if *configPath == "" {
		fatalf("-config is required")
	}
	if err := run(*configPath, *socksAddr, *bindDevice, *connectTimeout); err != nil {
		fatalf("%v", err)
	}
}

func run(configPath, socksAddr, bindDevice string, connectTimeout time.Duration) error {
	cfg, err := loadConfig(configPath)
	if err != nil {
		return err
	}
	bind, err := newBind(bindDevice)
	if err != nil {
		return err
	}
	localAddrs, err := parseAddresses(cfg.Interface.Addresses)
	if err != nil {
		return fmt.Errorf("interface addresses: %w", err)
	}
	dnsServers, err := parseAddresses(cfg.Interface.DNS)
	if err != nil {
		return fmt.Errorf("dns servers: %w", err)
	}
	mtu := cfg.Interface.MTU
	if mtu <= 0 {
		mtu = defaultMTU
	}
	tun, tnet, err := netstack.CreateNetTUN(localAddrs, dnsServers, mtu)
	if err != nil {
		return fmt.Errorf("create netstack tun: %w", err)
	}
	dev := device.NewDevice(tun, bind, device.NewLogger(device.LogLevelError, ""))
	uapi, err := buildUAPI(cfg, resolveEndpoints)
	if err != nil {
		dev.Close()
		return err
	}
	if err := dev.IpcSet(uapi); err != nil {
		dev.Close()
		return fmt.Errorf("apply uapi config: %w", err)
	}
	if err := dev.Up(); err != nil {
		dev.Close()
		return fmt.Errorf("bring tunnel up: %w", err)
	}
	log.Printf(
		"tunnel up: mtu=%d addresses=%s peers=%d socks=%s bind_device=%q",
		mtu, cfg.Interface.Addresses, len(cfg.Peers), socksAddr, bindDevice,
	)

	listener, err := net.Listen("tcp", socksAddr)
	if err != nil {
		dev.Close()
		return fmt.Errorf("listen on %s: %w", socksAddr, err)
	}
	server := &socksServer{
		listener: listener,
		dial:     tnet.DialContext,
		timeout:  connectTimeout,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		log.Print("shutting down")
		_ = listener.Close()
	}()
	if err := server.serve(); err != nil && !errors.Is(err, net.ErrClosed) {
		dev.Close()
		return err
	}
	dev.Close()
	return nil
}

func loadConfig(path string) (*tunnelConfig, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	var cfg tunnelConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	if cfg.Interface.PrivateKey == "" {
		return nil, errors.New("config has no interface.private_key")
	}
	if len(cfg.Interface.Addresses) == 0 {
		return nil, errors.New("config has no interface.addresses")
	}
	if len(cfg.Peers) == 0 {
		return nil, errors.New("config has no peers")
	}
	return &cfg, nil
}

// parseAddresses turns the CIDR / bare-address list from the INI config
// into the plain addresses gVisor assigns to the tun device. A prefix
// length is meaningless for a point-to-point userspace stack, so it is
// dropped.
func parseAddresses(raw []string) ([]netip.Addr, error) {
	addrs := make([]netip.Addr, 0, len(raw))
	for _, item := range raw {
		if addr, err := netip.ParseAddr(item); err == nil {
			addrs = append(addrs, addr)
			continue
		}
		prefix, err := netip.ParsePrefix(item)
		if err != nil {
			return nil, fmt.Errorf("%q is not an address or CIDR prefix", item)
		}
		addrs = append(addrs, prefix.Addr())
	}
	return addrs, nil
}

// resolveEndpoints turns each peer's "host:port" into an "ip:port"
// literal. amneziawg-go parses endpoints with netip.ParseAddrPort, which
// rejects hostnames outright, and the tunnel obviously cannot carry the
// DNS query that would resolve its own server. The lookup therefore has
// to happen here, on the host resolver, before IpcSet.
//
// Resolution happens once per run. A record that changes mid-speedtest
// (<= 20 s) would leave the tunnel with a stale peer address; that is
// acceptable for a measurement and far cheaper than re-resolving.
func resolveEndpoints(endpoint string) (string, error) {
	host, port, err := net.SplitHostPort(endpoint)
	if err != nil {
		return "", fmt.Errorf("endpoint %q must be host:port: %w", endpoint, err)
	}
	if _, err := strconv.ParseUint(port, 10, 16); err != nil {
		return "", fmt.Errorf("endpoint %q has an invalid port: %w", endpoint, err)
	}
	if addr, err := netip.ParseAddr(host); err == nil {
		return net.JoinHostPort(addr.String(), port), nil
	}
	ips, err := net.LookupIP(host)
	if err != nil {
		return "", fmt.Errorf("resolve endpoint %q: %w", endpoint, err)
	}
	for _, ip := range ips {
		if v4 := ip.To4(); v4 != nil {
			return net.JoinHostPort(v4.String(), port), nil
		}
	}
	for _, ip := range ips {
		return net.JoinHostPort(ip.String(), port), nil
	}
	return "", fmt.Errorf("resolve endpoint %q: no addresses returned", endpoint)
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "awgsocks: "+format+"\n", args...)
	os.Exit(1)
}
