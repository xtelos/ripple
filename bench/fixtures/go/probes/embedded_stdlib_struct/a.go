package a

import (
	"os"
	"strings"
	"sync"
)

type S struct {
	*os.File
	sync.Mutex
	strings.Builder
}

type Closer struct{}

func (Closer) Close() {}

func Use(s *S) {
	s.Lock()
	s.Unlock()
	s.Close()
	_ = s.Name()
	var c Closer
	c.Close()
}
