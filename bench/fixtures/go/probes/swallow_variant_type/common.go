package svt

type Linux struct{}

func (Linux) Close() {}

type Other struct{}

func (Other) Close() {}

func Use(t T) {
	t.Close()
}
