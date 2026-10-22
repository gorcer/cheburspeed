//go:build linux

package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"strconv"
	"sync"
	"syscall"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"golang.org/x/net/ipv4"
	"golang.org/x/net/ipv6"
	"golang.org/x/sys/unix"
)

// socketBufferSize mirrors conn.socketBufferSize in amneziawg-go. The
// kernel clamps it to net.core.{r,w}mem_max unless SO_*BUFFORCE is
// available (which needs CAP_NET_ADMIN, i.e. never for a probe), so this
// is best-effort but still worth asking for: the default 212 KiB caps a
// 20-second speedtest well below line rate.
const socketBufferSize = 7 << 20

var errFwmarkUnsupported = errors.New("fwmark is not supported by the interface-pinned bind")

// deviceBind is a conn.Bind that pins every datagram it sends to a
// single Linux network interface through SO_BINDTODEVICE. It exists
// because amneziawg-go's own StdNetBind creates its sockets internally
// and exposes no hook for socket options, while probes configured with
// NETWORK_INTERFACE (DAT-461) must egress through that NIC instead of
// the kernel's default route.
//
// It deliberately skips UDP GSO/GRO offload: the batching path in
// StdNetBind is what makes offload worthwhile, and a plain recvmmsg /
// sendto loop is already fast enough for a 20-second measurement. SO_*BUFFORCE
// is skipped for the same reason — it needs privileges the probe lacks.
type deviceBind struct {
	iface string

	mu      sync.Mutex
	v4      *net.UDPConn
	v6      *net.UDPConn
	v4Batch []ipv4.Message
	v6Batch []ipv6.Message
}

func newBind(iface string) (conn.Bind, error) {
	if iface == "" {
		return conn.NewDefaultBind(), nil
	}
	if _, err := net.InterfaceByName(iface); err != nil {
		return nil, fmt.Errorf("bind-device %q: %w", iface, err)
	}
	return &deviceBind{iface: iface}, nil
}

func (b *deviceBind) Open(port uint16) ([]conn.ReceiveFunc, uint16, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.v4 != nil || b.v6 != nil {
		return nil, 0, conn.ErrBindAlreadyOpen
	}
	lc := &net.ListenConfig{
		Control: func(_ string, _ string, c syscall.RawConn) error {
			var sockErr error
			err := c.Control(func(fd uintptr) {
				if sockErr = unix.SetsockoptString(
					int(fd), unix.SOL_SOCKET, unix.SO_BINDTODEVICE, b.iface,
				); sockErr != nil {
					return
				}
				// Best effort; the kernel silently clamps these.
				_ = unix.SetsockoptInt(int(fd), unix.SOL_SOCKET, unix.SO_RCVBUF, socketBufferSize)
				_ = unix.SetsockoptInt(int(fd), unix.SOL_SOCKET, unix.SO_SNDBUF, socketBufferSize)
			})
			if err != nil {
				return err
			}
			return sockErr
		},
	}

	v4, actual, err := listenUDP(lc, "udp4", port)
	if err != nil && !errors.Is(err, syscall.EAFNOSUPPORT) {
		return nil, 0, err
	}
	// Bind the v6 socket to the same port so a single endpoint covers
	// both families, exactly like StdNetBind does.
	v6, _, err6 := listenUDP(lc, "udp6", actual)
	if err6 != nil && !errors.Is(err6, syscall.EAFNOSUPPORT) && !errors.Is(err6, syscall.EADDRINUSE) {
		_ = v4.Close()
		return nil, 0, err6
	}

	var fns []conn.ReceiveFunc
	if v4 != nil {
		b.v4 = v4
		b.v4Batch = make([]ipv4.Message, conn.IdealBatchSize)
		fns = append(fns, b.makeReceiveIPv4(v4, b.v4Batch))
	}
	if v6 != nil {
		b.v6 = v6
		b.v6Batch = make([]ipv6.Message, conn.IdealBatchSize)
		fns = append(fns, b.makeReceiveIPv6(v6, b.v6Batch))
	}
	if len(fns) == 0 {
		return nil, 0, syscall.EAFNOSUPPORT
	}
	return fns, actual, nil
}

func listenUDP(lc *net.ListenConfig, network string, port uint16) (*net.UDPConn, uint16, error) {
	packetConn, err := lc.ListenPacket(context.Background(), network, ":"+strconv.Itoa(int(port)))
	if err != nil {
		return nil, 0, err
	}
	udpConn, ok := packetConn.(*net.UDPConn)
	if !ok {
		_ = packetConn.Close()
		return nil, 0, fmt.Errorf("unexpected connection type %T for %s", packetConn, network)
	}
	local, ok := packetConn.LocalAddr().(*net.UDPAddr)
	if !ok {
		_ = udpConn.Close()
		return nil, 0, fmt.Errorf("unexpected local address type %T", packetConn.LocalAddr())
	}
	return udpConn, uint16(local.Port), nil
}

// makeReceiveIPv4 reads a batch of datagrams into the caller-provided
// buffers. A zero entry in sizes marks a dropped packet, which is how
// the device skips unparsable addresses without losing the rest of the
// batch.
func (b *deviceBind) makeReceiveIPv4(c *net.UDPConn, batch []ipv4.Message) conn.ReceiveFunc {
	pc := ipv4.NewPacketConn(c)
	return func(packets [][]byte, sizes []int, eps []conn.Endpoint) (int, error) {
		n := min(len(packets), len(batch))
		msgs := batch[:n]
		for i := range msgs {
			msgs[i] = ipv4.Message{Buffers: net.Buffers{packets[i]}}
		}
		read, err := pc.ReadBatch(msgs, 0)
		if err != nil {
			return 0, err
		}
		for i := 0; i < read; i++ {
			addrPort, ok := udpAddrPort(msgs[i].Addr)
			if !ok {
				sizes[i] = 0
				continue
			}
			// x/net fills Message.N with the payload length and
			// Message.NN with the OOB byte count.
			sizes[i] = msgs[i].N
			eps[i] = &conn.StdNetEndpoint{AddrPort: addrPort}
		}
		return read, nil
	}
}

func (b *deviceBind) makeReceiveIPv6(c *net.UDPConn, batch []ipv6.Message) conn.ReceiveFunc {
	pc := ipv6.NewPacketConn(c)
	return func(packets [][]byte, sizes []int, eps []conn.Endpoint) (int, error) {
		n := min(len(packets), len(batch))
		msgs := batch[:n]
		for i := range msgs {
			msgs[i] = ipv6.Message{Buffers: net.Buffers{packets[i]}}
		}
		read, err := pc.ReadBatch(msgs, 0)
		if err != nil {
			return 0, err
		}
		for i := 0; i < read; i++ {
			addrPort, ok := udpAddrPort(msgs[i].Addr)
			if !ok {
				sizes[i] = 0
				continue
			}
			// x/net fills Message.N with the payload length and
			// Message.NN with the OOB byte count.
			sizes[i] = msgs[i].N
			eps[i] = &conn.StdNetEndpoint{AddrPort: addrPort}
		}
		return read, nil
	}
}

func udpAddrPort(addr net.Addr) (netip.AddrPort, bool) {
	udpAddr, ok := addr.(*net.UDPAddr)
	if !ok {
		return netip.AddrPort{}, false
	}
	return udpAddr.AddrPort(), true
}

// Close releases the sockets. It is not terminal: amneziawg-go calls
// BindUpdate (Close followed by Open) on every Up and on every
// listen_port change, exactly like StdNetBind.
func (b *deviceBind) Close() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	var errs []error
	for _, c := range []*net.UDPConn{b.v4, b.v6} {
		if c != nil {
			errs = append(errs, c.Close())
		}
	}
	b.v4 = nil
	b.v6 = nil
	return errors.Join(errs...)
}

func (b *deviceBind) SetMark(uint32) error {
	return errFwmarkUnsupported
}

func (b *deviceBind) Send(bufs [][]byte, ep conn.Endpoint) error {
	endpoint, ok := ep.(*conn.StdNetEndpoint)
	if !ok {
		return conn.ErrWrongEndpointType
	}
	b.mu.Lock()
	c := b.v4
	if endpoint.DstIP().Is6() {
		c = b.v6
	}
	b.mu.Unlock()
	if c == nil {
		return syscall.EAFNOSUPPORT
	}
	addr := net.UDPAddr{IP: endpoint.DstIP().Unmap().AsSlice(), Port: int(endpoint.Port())}
	for _, buf := range bufs {
		if _, err := c.WriteToUDP(buf, &addr); err != nil {
			return err
		}
	}
	return nil
}

func (b *deviceBind) ParseEndpoint(s string) (conn.Endpoint, error) {
	addrPort, err := netip.ParseAddrPort(s)
	if err != nil {
		return nil, err
	}
	return &conn.StdNetEndpoint{AddrPort: addrPort}, nil
}

func (b *deviceBind) BatchSize() int {
	return conn.IdealBatchSize
}
