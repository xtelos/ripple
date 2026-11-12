package omf

import (
	"net/http"
	"strings"
	"sync"
)

func Use(c *http.Client, r *http.Request, p *sync.Pool, b *strings.Builder) {
	c.CloseIdleConnections()
	c.CheckRedirect(r, nil)
	r.GetBody()
	p.Get()
	p.New()
	b.WriteString("x")
}
