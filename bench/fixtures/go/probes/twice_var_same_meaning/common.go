package tvs

type Std struct{}

func (*Std) Close() {}

func Use() {
	Logger.Close()
}
