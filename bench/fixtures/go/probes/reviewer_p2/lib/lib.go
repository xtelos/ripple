package lib

type Thing struct{}

func (Thing) Close() error   { return nil }
func (Thing) String() string { return "" }
func (*Thing) Len() int      { return 0 }
func (Thing) Write(p []byte) (int, error) { return 0, nil }

func New() *Thing { return &Thing{} }
func Helper()     {}

type Box[T any] struct{ v T }

func (b Box[T]) Get() T  { return b.v }
func (b *Box[T]) Put(v T) { b.v = v }
func Make[T any](v T) Box[T] { return Box[T]{v} }
func Map[T, U any](v T, f func(T) U) U { return f(v) }

type Runner interface{ Run() }

type Impl struct{}

func (Impl) Run() {}

type Num int

func (Num) Double() Num { return 0 }
