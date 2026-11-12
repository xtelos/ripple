package a

func platform() {}

func (T) Name() string { return "windows" }

func init() { initOnly() }

func initOnly() {}
