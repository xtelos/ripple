from warehouse.schema import Line, local_only, priced, tagged


def test_definitions_run_their_annotations_once():
    assert priced(3) == 3
    assert Line().total(2) == 2
    assert tagged() == 1
    assert local_only() == 1
