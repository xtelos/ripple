package calc

import "testing"

func TestGrid(t *testing.T) {
	for _, row := range []string{"x", "y"} {
		t.Run(row, func(t *testing.T) {
			for _, col := range []string{"1", "2"} {
				t.Run(col, func(t *testing.T) {
					if (row == "x" && col == "2") || (row == "y" && col == "1") {
						t.Errorf("%s/%s was not asked for", row, col)
					}
				})
			}
		})
	}
}
