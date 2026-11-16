module example.com/impl_chain_published

go 1.22

require example.com/a v0.0.0

require example.com/other v1.3.0 // indirect

replace example.com/a => ./a
