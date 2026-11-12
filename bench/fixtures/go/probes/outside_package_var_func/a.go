package opv

import (
	"flag"
	"log"
	"net/http"
	"os"
	"sort"
)

func hook() {}

func Use(xs []int) {
	flag.Usage = hook
	flag.Usage()
	flag.Parse()
	log.Println("x")
	sort.Slice(xs, func(i, j int) bool { return xs[i] < xs[j] })
	http.DefaultServeMux.HandleFunc("/", nil)
	os.Stdout.WriteString("x")
	_ = os.Getenv("X")
}
