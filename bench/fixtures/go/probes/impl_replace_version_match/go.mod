module example.com/impl_replace_version_match

go 1.22

require example.com/other v1.2.0

replace example.com/other v1.2.0 => ./b
