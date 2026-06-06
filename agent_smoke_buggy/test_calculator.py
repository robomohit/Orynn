from calculator import add, average, multiply


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_multiply():
    assert multiply(4, 3) == 12
    assert multiply(-2, 5) == -10


def test_average():
    assert average([2, 4, 6]) == 4
    assert average([10]) == 10
