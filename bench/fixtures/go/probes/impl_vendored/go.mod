module example.com/impl_vendored

go 1.22

require example.com/other v1.0.0

replace example.com/other => ./b
