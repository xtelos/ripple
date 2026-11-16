package a

// A call through an interface has no proven target. A type that has every method of the interface, with the same
// signature, may be what the value holds: the call "may" reach its method. Each call line ends with a want comment that
// says which methods ripple must name as such, "none" when it must name no method at all.

type Store interface {
	Get(key string) (string, error)
	Put(key string, value string) error
}

type Mem struct{ m map[string]string }

func (s *Mem) Get(key string) (string, error) { return s.m[key], nil }
func (s *Mem) Put(key, value string) error    { s.m[key] = value; return nil }

type Disk struct{}

func (Disk) Get(key string) (string, error) { return "", nil }
func (Disk) Put(key string, value string) error { return nil }

// the same signatures, written with named results
type Named struct{}

func (Named) Get(k string) (v string, err error) { return }
func (Named) Put(a, b string) (err error)        { return }

// lacks Put
type ReadOnly struct{}

func (ReadOnly) Get(key string) (string, error) { return "", nil }

// Put has another result
type WrongResult struct{}

func (WrongResult) Get(key string) (string, error) { return "", nil }
func (WrongResult) Put(key, value string) bool     { return false }

// Get has another parameter type
type WrongParam struct{}

func (WrongParam) Get(key []byte) (string, error) { return "", nil }
func (WrongParam) Put(key, value string) error    { return nil }

// Put has fewer parameters
type WrongCount struct{}

func (WrongCount) Get(key string) (string, error) { return "", nil }
func (WrongCount) Put(key string) error           { return nil }

// results in another order
type WrongOrder struct{}

func (WrongOrder) Get(key string) (error, string) { return nil, "" }
func (WrongOrder) Put(key, value string) error    { return nil }

// Put is a field, not a method
type PutField struct{ Put func(string, string) error }

func (PutField) Get(key string) (string, error) { return "", nil }

type Logger interface {
	Log(format string, args ...any)
}

type Std struct{}

func (Std) Log(format string, args ...any) {}

// `any` is `interface{}`
type Spelled struct{}

func (Spelled) Log(format string, args ...interface{}) {}

// a slice is not a variadic parameter
type Sliced struct{}

func (Sliced) Log(format string, args []any) {}

type holder struct {
	store Store
}

func New() Store { return &Mem{} }

func Use(s Store, l Logger, h holder) {
	s.Get("k")      // want: Mem.Get, Disk.Get, Named.Get
	s.Put("k", "v") // want: Mem.Put, Disk.Put, Named.Put
	l.Log("x")      // want: Std.Log, Spelled.Log
	h.store.Get("k") // want: Mem.Get, Disk.Get, Named.Get
	New().Put("k", "v") // want: Mem.Put, Disk.Put, Named.Put
	var local Store = Disk{}
	local.Get("k") // want: Mem.Get, Disk.Get, Named.Get
}
