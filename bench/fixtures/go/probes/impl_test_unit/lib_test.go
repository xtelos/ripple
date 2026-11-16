package lib

import (
	"testing"

	"example.com/impl_test_unit/internal/helper"
)

type Fake struct{}

func (Fake) Do() {}

func TestCall(t *testing.T) {
	var d Doer = helper.H{}
	d.Do() // want: Own.Do, Fake.Do, internal/helper:H.Do
}
