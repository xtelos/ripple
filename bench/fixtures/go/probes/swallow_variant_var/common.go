package swvv

type Linux struct{}

func (Linux) Close() {}

type Other struct{}

func (Other) Close() {}

func Use() {
	X.Close()
}
