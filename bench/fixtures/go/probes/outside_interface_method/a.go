package oim

import "io"

type reader struct{}

func (reader) Read(p []byte) (int, error) { return 0, nil }

func Use(r io.Reader, w io.Writer, rc io.ReadCloser, e error) {
	r.Read(nil)
	w.Write(nil)
	rc.Close()
	_ = e.Error()
	var rr = reader{}
	rr.Read(nil)
}
