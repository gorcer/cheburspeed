package main

import (
	"errors"
	"strings"
	"testing"
)

func intPtr(v int) *int { return &v }

// literalResolver keeps buildUAPI tests free of DNS.
func literalResolver(endpoint string) (string, error) { return "203.0.113.7:40974", nil }

func TestBuildUAPIForwardsAwgObfuscation(t *testing.T) {
	cfg := &tunnelConfig{
		Interface: ifaceConfig{
			PrivateKey: "0f8a1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
			Addresses:  []string{"10.8.40.106/32"},
			DNS:        []string{"1.1.1.1"},
			MTU:        1420,
			JC:         intPtr(3),
			JMin:       intPtr(10),
			JMax:       intPtr(50),
			S1:         intPtr(150),
			S2:         intPtr(224),
			S3:         intPtr(0),
			S4:         intPtr(0),
			H1:         "1232863036",
			H2:         "1441878557",
			H3:         "636923766",
			H4:         "214107436",
		},
		Peers: []peerConfig{
			{
				PublicKey:           "fgI6VqMCIvjErRo2vL6XQhDqfBiK+FTxLBRLxMCCZVU=",
				Endpoint:            "in.oneyearsolution.com:40974",
				PresharedKey:        "pkcDjyh0jW8RFTLvhEEeMk4SPT7WUEPGyN/ZXGQE2Vk=",
				AllowedIPs:          []string{"0.0.0.0/0", "::/0"},
				PersistentKeepalive: "25",
			},
		},
	}

	got, err := buildUAPI(cfg, literalResolver)
	if err != nil {
		t.Fatalf("buildUAPI: %v", err)
	}

	for _, want := range []string{
		"private_key=0f8a1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b\n",
		"listen_port=0\n",
		"jc=3\n",
		"jmin=10\n",
		"jmax=50\n",
		"s1=150\n",
		"s2=224\n",
		"s3=0\n",
		"s4=0\n",
		"h1=1232863036\n",
		"h4=214107436\n",
		"public_key=fgI6VqMCIvjErRo2vL6XQhDqfBiK+FTxLBRLxMCCZVU=\n",
		"preshared_key=pkcDjyh0jW8RFTLvhEEeMk4SPT7WUEPGyN/ZXGQE2Vk=\n",
		// amneziawg-go only parses IP literals, so the hostname from
		// the INI config must already be resolved by the caller.
		"endpoint=203.0.113.7:40974\n",
		"replace_allowed_ips=true\n",
		"allowed_ip=0.0.0.0/0\n",
		"allowed_ip=::/0\n",
		"persistent_keepalive_interval=25\n",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("uapi payload is missing %q\ngot:\n%s", want, got)
		}
	}

	if !strings.HasSuffix(got, "\n\n") {
		t.Errorf("uapi payload must end with a blank line to commit the operation\ngot:\n%s", got)
	}
}

func TestBuildUAPIOmitsAbsentObfuscation(t *testing.T) {
	// A WireGuard-style config without AWG knobs must not emit empty
	// values: amneziawg-go rejects "jc=" with a parse error.
	cfg := &tunnelConfig{
		Interface: ifaceConfig{
			PrivateKey: "aa",
			Addresses:  []string{"10.0.0.2/32"},
		},
		Peers: []peerConfig{{PublicKey: "bb", Endpoint: "example.com:51820"}},
	}
	got, err := buildUAPI(cfg, literalResolver)
	if err != nil {
		t.Fatalf("buildUAPI: %v", err)
	}
	for _, unwanted := range []string{"jc=", "s1=", "h1=", "i1=", "preshared_key="} {
		if strings.Contains(got, unwanted) {
			t.Errorf("uapi payload should not contain %q\ngot:\n%s", unwanted, got)
		}
	}
	if !strings.Contains(got, "allowed_ip=") && !strings.Contains(got, "replace_allowed_ips=true") {
		t.Errorf("peer block must reset its allowed-ips table\ngot:\n%s", got)
	}
}

func TestBuildUAPIRejectsIncompletePeer(t *testing.T) {
	boom := errors.New("resolve endpoint \"nope\": no such host")
	resolve := func(string) (string, error) { return "", boom }

	tests := map[string]peerConfig{
		"missing public key": {Endpoint: "example.com:51820"},
		"missing endpoint":   {PublicKey: "bb"},
	}
	for name, peer := range tests {
		cfg := &tunnelConfig{
			Interface: ifaceConfig{PrivateKey: "aa", Addresses: []string{"10.0.0.2/32"}},
			Peers:     []peerConfig{peer},
		}
		if _, err := buildUAPI(cfg, resolve); err == nil {
			t.Errorf("%s: buildUAPI must fail", name)
		}
	}

	cfg := &tunnelConfig{
		Interface: ifaceConfig{PrivateKey: "aa", Addresses: []string{"10.0.0.2/32"}},
		Peers:     []peerConfig{{PublicKey: "bb", Endpoint: "example.com:51820"}},
	}
	_, err := buildUAPI(cfg, resolve)
	if err == nil || !strings.Contains(err.Error(), "no such host") {
		t.Errorf("resolver failure must surface, got %v", err)
	}
}

func TestParseAddressesAcceptsBareAndCIDR(t *testing.T) {
	got, err := parseAddresses([]string{"10.8.40.106/32", "fd00::1"})
	if err != nil {
		t.Fatalf("parseAddresses: %v", err)
	}
	want := []string{"10.8.40.106", "fd00::1"}
	if len(got) != len(want) {
		t.Fatalf("got %d addresses, want %d", len(got), len(want))
	}
	for i, addr := range got {
		if addr.String() != want[i] {
			t.Errorf("address %d = %s, want %s", i, addr, want[i])
		}
	}
	if _, err := parseAddresses([]string{"not-an-address"}); err == nil {
		t.Error("parseAddresses must reject garbage")
	}
}

func TestResolveEndpointsPassesThroughLiterals(t *testing.T) {
	got, err := resolveEndpoints("203.0.113.7:40974")
	if err != nil {
		t.Fatalf("resolveEndpoints: %v", err)
	}
	if got != "203.0.113.7:40974" {
		t.Errorf("got %q, want the literal unchanged", got)
	}
	if _, err := resolveEndpoints("203.0.113.7"); err == nil {
		t.Error("an endpoint without a port must be rejected")
	}
	if _, err := resolveEndpoints("example.com:not-a-port"); err == nil {
		t.Error("a non-numeric port must be rejected")
	}
}

func TestResolveEndpointsRejectsUnknownHost(t *testing.T) {
	// .invalid is reserved by RFC 2606 and can never resolve.
	_, err := resolveEndpoints("awgsocks-test.invalid:40974")
	if err == nil {
		t.Fatal("resolveEndpoints must report an unresolvable host")
	}
	if !strings.Contains(err.Error(), "awgsocks-test.invalid") {
		t.Errorf("error should name the endpoint, got %v", err)
	}
}
