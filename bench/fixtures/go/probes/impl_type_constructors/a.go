package a

// Two type expressions have one canonical text only when the spec makes them one type: a pointer to an array is not an array of
// pointers, a slice of pointers to slices is not a pointer to slices of slices, parentheses do not matter, and a function type
// is never followed (so IM has no edge though TM has the method).

type TA struct{}

func (TA) M(x *[3]int) {}

type TB struct{}

func (TB) M(x [3]*int) {}

type TC struct{}

func (TC) M(x *int) {}

type TD struct{}

func (TD) M(x []*[]int) {}

type TE struct{}

func (TE) M(x *[][]int) {}

type TF struct{}

func (TF) M(x map[string]chan<- int) {}

type TG struct{}

func (TG) M(x map[string]<-chan int) {}

type TH struct{}

func (TH) M(x chan chan int) {}

type TI struct{}

func (TI) M(x []int) {}

type TK struct{}

func (TK) M(x [2][3]int) {}

type TL struct{}

func (TL) M(x [3][2]int) {}

type TM struct{}

func (TM) M(x func() int) {}

type TN struct{}

func (TN) M(x map[int][]string) {}

type TP struct{}

func (TP) M(x map[[2]int]string) {}

type TQ struct{}

func (TQ) M(x []map[string]int) {}

type IA interface{ M(x *[3]int) }

type IB interface{ M(x [3]*int) }

type IC interface{ M(x (*int)) }

type ID interface{ M(x []*[]int) }

type IE interface{ M(x *[][]int) }

type IF interface{ M(x map[string]chan<- int) }

type IG interface{ M(x map[string]<-chan int) }

type IH interface{ M(x chan (chan int)) }

type II interface{ M(x ([]int)) }

type IK interface{ M(x [2][3]int) }

type IL interface{ M(x [3][2]int) }

type IM interface{ M(x func() int) }

type IN interface{ M(x map[int][]string) }

type IP interface{ M(x map[[2]int]string) }

type IQ interface{ M(x []map[string]int) }

type FR interface{ R() func() func() int }

type TFR struct{}

func (TFR) R() func() func() int { return nil }

func Use(vIA IA, xIA *[3]int, vIB IB, xIB [3]*int, vIC IC, xIC (*int), vID ID, xID []*[]int, vIE IE, xIE *[][]int, vIF IF, xIF map[string]chan<- int, vIG IG, xIG map[string]<-chan int, vIH IH, xIH chan (chan int), vII II, xII ([]int), vIK IK, xIK [2][3]int, vIL IL, xIL [3][2]int, vIM IM, xIM func() int, vIN IN, xIN map[int][]string, vIP IP, xIP map[[2]int]string, vIQ IQ, xIQ []map[string]int, fr FR) {
	vIA.M(xIA) // want: TA.M
	vIB.M(xIB) // want: TB.M
	vIC.M(xIC) // want: TC.M
	vID.M(xID) // want: TD.M
	vIE.M(xIE) // want: TE.M
	vIF.M(xIF) // want: TF.M
	vIG.M(xIG) // want: TG.M
	vIH.M(xIH) // want: TH.M
	vII.M(xII) // want: TI.M
	vIK.M(xIK) // want: TK.M
	vIL.M(xIL) // want: TL.M
	vIM.M(xIM) // want: none
	vIN.M(xIN) // want: TN.M
	vIP.M(xIP) // want: TP.M
	vIQ.M(xIQ) // want: TQ.M
	fr.R() // want: none
}
