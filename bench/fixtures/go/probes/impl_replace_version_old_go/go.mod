module example.com/impl_replace_version_old_go

go 1.16

require example.com/other v1.2.0

replace example.com/other v1.2.0 => ./b
