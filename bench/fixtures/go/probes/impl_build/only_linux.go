package a

// the type itself is linux's: nothing of it exists on the other platforms
type LinuxOnly struct{}

func (LinuxOnly) Start() {}
func (LinuxOnly) Stop()  {}
