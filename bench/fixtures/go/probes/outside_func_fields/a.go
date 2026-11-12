package off

import (
	"crypto/tls"
	"flag"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"syscall"
	"testing"
)

func hook() {}

func proxy(*http.Request) (*url.URL, error) { hook(); return nil, nil }

func control(network, address string, c syscall.RawConn) error { hook(); return nil }

func getCert(*tls.ClientHelloInfo) (*tls.Certificate, error) { hook(); return nil, nil }

func UseTransport(r *http.Request) {
	t := &http.Transport{Proxy: proxy}
	t.Proxy(r)
}

func UseDialer() {
	d := net.Dialer{Control: control}
	d.Control("tcp", "x", nil)
}

func UseHTTPTestConfig() {
	ts := httptest.NewUnstartedServer(nil)
	ts.Config.ConnState = func(net.Conn, http.ConnState) { hook() }
	ts.Config.ConnState(nil, http.StateNew)
}

func UseHTTPTestTLS() {
	ts := httptest.NewUnstartedServer(nil)
	ts.TLS = &tls.Config{GetCertificate: getCert}
	ts.TLS.GetCertificate(nil)
}

func UseFlagUsage() {
	flag.Usage = hook
	flag.Usage()
}

func UseDefaultClient(r *http.Request) {
	http.DefaultClient.CheckRedirect = func(*http.Request, []*http.Request) error { hook(); return nil }
	http.DefaultClient.CheckRedirect(r, nil)
}

func UseInternalTest() {
	it := testing.InternalTest{Name: "x", F: func(*testing.T) { hook() }}
	it.F(nil)
}
