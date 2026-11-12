package a2

var cfg = newCfg()

var thing2 = MakeBox[Thing]()

type Cfg struct{}

func (*Cfg) Run() {}

func newCfg() *Cfg { return &Cfg{} }

func init() { cfg.Run() }

func init() { thing2.Get().M() }

func UseVars() {
	cfg.Run()
	thing2.Get().M()
}
