package oep

import (
	"io"
	"net/http"
)

type Srv struct{ *http.Server }

type Rd struct{ io.Reader }

type impl struct{}

func (impl) Read(p []byte) (int, error) { return 0, nil }

func Use(s Srv, r Rd) {
	s.Close()
	s.ConnState(nil, 0)
	s.RegisterOnShutdown(func() {})
	r.Read(nil)
}
