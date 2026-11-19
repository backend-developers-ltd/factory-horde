"""Small dependency-free library control."""


def mean(values):
    if not values:
        raise ValueError("empty")
    return sum(values) / len(values)


def unique(values):
    return list(dict.fromkeys(values))
