package a

type Writer interface{ Write() }

type Impl struct{}

func (Impl) Write()  {}
func (*Impl) Close() {}

var w Writer = Impl{}
var hook = func() {}
var impl = &Impl{}
var chain = impl
var typed *Impl = impl

func newImpl() *Impl { return &Impl{} }

var viaCall = newImpl()
var fns = []func(){hook}
var callback func()

func init() { callback = hook }

func Use() {
	w.Write()
	hook()
	impl.Close()
	chain.Close()
	typed.Close()
	viaCall.Close()
	fns[0]()
	callback()
}
