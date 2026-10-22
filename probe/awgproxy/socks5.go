package main

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"strconv"
	"sync"
	"time"
)

// SOCKS5 wire constants (RFC 1928 / RFC 1929).
const (
	socks5Version = 0x05

	socks5AuthNone     = 0x00
	socks5AuthNoAccept = 0xFF

	socks5CmdConnect = 0x01

	socks5AddrIPv4   = 0x01
	socks5AddrDomain = 0x03
	socks5AddrIPv6   = 0x04

	socks5ReplySuccess        = 0x00
	socks5ReplyGeneralFailure = 0x01
)

// socksServer is a minimal SOCKS5 proxy that supports CONNECT only and
// dials through the tunnel's userspace network stack. The probe's HTTP
// client only ever issues CONNECT, so BIND/UDP ASSOCIATE are rejected
// rather than half-implemented.
type socksServer struct {
	listener net.Listener
	dial     func(ctx context.Context, network, address string) (net.Conn, error)
	timeout  time.Duration

	wg sync.WaitGroup
}

func (s *socksServer) serve() error {
	for {
		conn, err := s.listener.Accept()
		if err != nil {
			s.wg.Wait()
			return err
		}
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			if err := s.handle(conn); err != nil {
				log.Printf("socks: %v", err)
			}
		}()
	}
}

func (s *socksServer) handle(client net.Conn) error {
	defer client.Close()
	if err := client.SetDeadline(time.Now().Add(s.timeout)); err != nil {
		return err
	}
	if err := s.negotiate(client); err != nil {
		return err
	}
	target, err := readConnectRequest(client)
	if err != nil {
		// The reply has to go out even for a malformed request so the
		// client stops waiting instead of hitting its own timeout.
		_ = writeReply(client, socks5ReplyGeneralFailure)
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), s.timeout)
	defer cancel()
	remote, err := s.dial(ctx, "tcp", target)
	if err != nil {
		_ = writeReply(client, socks5ReplyGeneralFailure)
		return fmt.Errorf("dial %s through tunnel: %w", target, err)
	}
	defer remote.Close()
	if err := writeReply(client, socks5ReplySuccess); err != nil {
		return err
	}
	// From here on both directions are plain streams, so the handshake
	// deadline has to go: a speedtest download legitimately runs for
	// longer than the connect timeout.
	if err := client.SetDeadline(time.Time{}); err != nil {
		return err
	}
	return relay(client, remote)
}

func (s *socksServer) negotiate(client net.Conn) error {
	header := make([]byte, 2)
	if _, err := io.ReadFull(client, header); err != nil {
		return fmt.Errorf("read auth header: %w", err)
	}
	if header[0] != socks5Version {
		return fmt.Errorf("unsupported socks version %d", header[0])
	}
	methods := make([]byte, int(header[1]))
	if _, err := io.ReadFull(client, methods); err != nil {
		return fmt.Errorf("read auth methods: %w", err)
	}
	supported := false
	for _, method := range methods {
		if method == socks5AuthNone {
			supported = true
			break
		}
	}
	if !supported {
		_, _ = client.Write([]byte{socks5Version, socks5AuthNoAccept})
		return errors.New("client requires authentication, which the probe does not offer")
	}
	if _, err := client.Write([]byte{socks5Version, socks5AuthNone}); err != nil {
		return fmt.Errorf("write auth reply: %w", err)
	}
	return nil
}

// readConnectRequest parses a CONNECT request and returns the
// "host:port" target. Domain names are passed through verbatim so the
// tunnel's own resolver handles them, which is the whole point of
// routing DNS through the config's DNS servers.
func readConnectRequest(client net.Conn) (string, error) {
	header := make([]byte, 4)
	if _, err := io.ReadFull(client, header); err != nil {
		return "", fmt.Errorf("read request header: %w", err)
	}
	if header[0] != socks5Version {
		return "", fmt.Errorf("unsupported socks version %d", header[0])
	}
	if header[1] != socks5CmdConnect {
		return "", fmt.Errorf("unsupported command %d (only CONNECT is served)", header[1])
	}
	var host string
	switch header[3] {
	case socks5AddrIPv4:
		raw := make([]byte, 4)
		if _, err := io.ReadFull(client, raw); err != nil {
			return "", fmt.Errorf("read ipv4 address: %w", err)
		}
		host = net.IP(raw).String()
	case socks5AddrIPv6:
		raw := make([]byte, 16)
		if _, err := io.ReadFull(client, raw); err != nil {
			return "", fmt.Errorf("read ipv6 address: %w", err)
		}
		host = net.IP(raw).String()
	case socks5AddrDomain:
		length := make([]byte, 1)
		if _, err := io.ReadFull(client, length); err != nil {
			return "", fmt.Errorf("read domain length: %w", err)
		}
		raw := make([]byte, int(length[0]))
		if _, err := io.ReadFull(client, raw); err != nil {
			return "", fmt.Errorf("read domain: %w", err)
		}
		host = string(raw)
	default:
		return "", fmt.Errorf("unsupported address type %d", header[3])
	}
	rawPort := make([]byte, 2)
	if _, err := io.ReadFull(client, rawPort); err != nil {
		return "", fmt.Errorf("read port: %w", err)
	}
	return net.JoinHostPort(host, strconv.Itoa(int(binary.BigEndian.Uint16(rawPort)))), nil
}

func writeReply(client net.Conn, code byte) error {
	_, err := client.Write([]byte{socks5Version, code, 0x00, socks5AddrIPv4, 0, 0, 0, 0, 0, 0})
	return err
}

// relay copies in both directions and returns once either side is done.
// The deferred closes in the caller tear the other direction down.
func relay(client, remote net.Conn) error {
	var wg sync.WaitGroup
	wg.Add(2)
	copyFn := func(dst net.Conn, src net.Conn) {
		defer wg.Done()
		if _, err := io.Copy(dst, src); err != nil {
			log.Printf("socks: relay finished: %v", err)
		}
		if closer, ok := dst.(interface{ CloseWrite() error }); ok {
			_ = closer.CloseWrite()
		}
	}
	go copyFn(remote, client)
	go copyFn(client, remote)
	wg.Wait()
	return nil
}
