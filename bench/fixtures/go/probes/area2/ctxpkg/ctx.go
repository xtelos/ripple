package context

type Context struct{}

func (Context) Done() {}

func Background() Context { return Context{} }
