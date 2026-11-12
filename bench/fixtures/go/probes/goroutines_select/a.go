package a

type T struct{}

func (T) M() {}

func work(ch chan T, done chan struct{}) {
	t := T{}
	go t.M()
	select {
	case v := <-ch:
		v.M()
	case ch <- t:
		t.M()
	case <-done:
	}
	for v := range ch {
		v.M()
	}
	v, ok := <-ch
	_ = ok
	v.M()
}
