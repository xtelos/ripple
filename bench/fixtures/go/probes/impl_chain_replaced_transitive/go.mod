module example.com/impl_chain_replaced_transitive

go 1.22

require (
	example.com/a v0.0.0
	example.com/other v1.3.0
)

replace (
	example.com/a => ./a
	example.com/other => ./b
)
