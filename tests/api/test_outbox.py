"""Delivery after a request (src/pms/api/outbox.py): one at a time in a process.

docs/edge-cases.md H-016, P-075: after every write the API delivered what waited in the outbox
on a pooled connection, each email waiting up to 20 s on a slow or hung mail server; a handful of
writes started a handful of deliveries and filled the pool, and everyone was told the PMS was
busy. Now a delivery (and autopay's submission to the payments partner) runs one at a time in a
process: a write while one runs makes it run once more after, so nothing written waits for the
next write, and the pool lends it one connection at most."""

import contextlib
import threading
import time

import pytest

from pms import email
from pms.api import autopay, outbox


@pytest.mark.timing
def test_calls_while_a_job_runs_return_at_once_and_it_runs_once_more_after():
    once = outbox.OneAtATime()
    gate, started = threading.Event(), threading.Event()
    runs: list[int] = []

    def job() -> None:
        runs.append(1)
        started.set()
        assert gate.wait(10)

    first = threading.Thread(target=once.run, args=(job,))
    first.start()
    assert started.wait(10)
    began = time.monotonic()
    for _ in range(5):
        once.run(job)  # would wait on the gate if it ran here
    assert time.monotonic() - began < 1
    gate.set()
    first.join(10)

    # The first, and once more for the five calls meanwhile.
    assert len(runs) == 2
    once.run(job)
    assert len(runs) == 3


def test_a_job_that_fails_lets_the_next_call_run_it():
    once = outbox.OneAtATime()
    calls: list[int] = []

    def broken() -> None:
        calls.append(1)
        raise RuntimeError("the mail server said no")

    with pytest.raises(RuntimeError):
        once.run(broken)
    with pytest.raises(RuntimeError):
        once.run(broken)
    assert len(calls) == 2


class Pool:
    """A pool that counts the connections lent at once."""

    def __init__(self) -> None:
        self.lent = self.most = self.total = 0
        self.lock = threading.Lock()

    @contextlib.contextmanager
    def connection(self):
        with self.lock:
            self.lent += 1
            self.total += 1
            self.most = max(self.most, self.lent)

        class Conn:
            autocommit = False

        try:
            yield Conn()
        finally:
            with self.lock:
                self.lent -= 1


@pytest.mark.timing
@pytest.mark.parametrize("which", ["email", "autopay"])
def test_many_writes_at_once_lend_the_slow_partner_one_connection(monkeypatch, which):
    pool, gate, started = Pool(), threading.Event(), threading.Event()

    def slow(conn, sender, *more):
        started.set()
        assert gate.wait(10)

    if which == "email":
        monkeypatch.setattr(email, "deliver_pending", slow)
        deliver = outbox.deliver
    else:
        monkeypatch.setattr(autopay.autopay, "submit_waiting", slow)
        deliver = autopay.submit
    writes = [threading.Thread(target=deliver, args=(pool, email.FakeSender())) for _ in range(8)]
    writes[0].start()
    assert started.wait(10)
    for write in writes[1:]:
        write.start()
    for write in writes[1:]:
        write.join(10)
        assert not write.is_alive()  # returned at once: the running one goes again for them
    gate.set()
    writes[0].join(10)

    assert (pool.most, pool.total) == (1, 2)
