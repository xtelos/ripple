package a

type Shape interface{ Area() int }

type Sq struct{}

func (Sq) Area() int { return 1 }
func (Sq) Side() int { return 1 }

type Named interface {
	Shape
	Name() string
}

type Circle struct{ Shape }

func Use(s Shape, n Named, v any) {
	s.Area()
	n.Area()
	n.Name()
	if sq, ok := s.(Sq); ok {
		sq.Side()
	}
	s.(Sq).Side()
	s.(Named).Name()
	v.(Shape).Area()
	switch x := s.(type) {
	case Sq:
		x.Side()
	case Named:
		x.Name()
	case Circle:
		x.Area()
	}
	var c Circle
	c.Area()
	var sh Shape = Sq{}
	sh.Area()
	any(Sq{}).(Sq).Area()
}
