"""What can go wrong when asking the carrier."""


class CarrierError(Exception):
    """The carrier answered, and the answer was not useful.

    A 401 because a key is wrong, a 400 because a request was malformed. Retrying
    will produce the same answer, so nothing here is retried.
    """


class CarrierUnavailable(CarrierError):
    """The carrier did not answer, or answered with its own failure.

    A timeout, a refused connection, a 500, or a circuit that is open. These may
    succeed later, which is the whole distinction: it is the difference between
    "try again" and "stop asking".
    """
