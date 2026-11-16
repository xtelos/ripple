package d

import (
	htmltemplate "html/template"
	"text/template"
)

// text/template.FuncMap and html/template.FuncMap are two types, whatever the file calls the package
type Mapper interface{ Map(template.FuncMap) }

type Text struct{}

func (Text) Map(template.FuncMap) {}

type HTML struct{}

func (HTML) Map(htmltemplate.FuncMap) {}

func Use(m Mapper) {
	m.Map(nil) // want: d:Text.Map
}
