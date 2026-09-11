import time

from vagscan.gui.io_worker import IOWorker


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_submit_runs_job_and_poll_reports_success():
    results = []
    worker = IOWorker()
    worker.submit(lambda: 42, lambda ok, result: results.append((ok, result)))
    assert _wait_until(lambda: (worker.poll() or results))
    assert results == [(True, 42)]


def test_submit_reports_exception_without_raising_on_worker_thread():
    results = []
    worker = IOWorker()

    def boom():
        raise ValueError("bus error")

    worker.submit(boom, lambda ok, result: results.append((ok, result)))
    assert _wait_until(lambda: (worker.poll() or results))
    ok, result = results[0]
    assert ok is False
    assert isinstance(result, ValueError)


def test_jobs_run_strictly_one_at_a_time_in_submission_order():
    """Guards the whole reason this class exists: two overlapping requests
    to a half-duplex adapter would corrupt each other."""
    order = []
    worker = IOWorker()
    done = []

    def make_job(n, delay):
        def job():
            order.append(("start", n))
            time.sleep(delay)
            order.append(("end", n))
            return n

        return job

    worker.submit(make_job(1, 0.05), lambda ok, result: done.append(result))
    worker.submit(make_job(2, 0.01), lambda ok, result: done.append(result))
    worker.submit(make_job(3, 0.0), lambda ok, result: done.append(result))
    assert _wait_until(lambda: (worker.poll() or len(done) == 3))
    assert order == [
        ("start", 1),
        ("end", 1),
        ("start", 2),
        ("end", 2),
        ("start", 3),
        ("end", 3),
    ]


def test_poll_does_not_mix_up_results_across_jobs():
    """Regression guard: each completed job's (ok, result) must reach its
    own callback, even when several finish before poll() is next called."""
    seen = []
    worker = IOWorker()
    for i in range(5):
        worker.submit((lambda v=i: v), lambda ok, result: seen.append(result))
    assert _wait_until(lambda: (worker.poll() or len(seen) == 5))
    assert seen == [0, 1, 2, 3, 4]


def test_poll_is_a_no_op_when_nothing_finished():
    worker = IOWorker()
    worker.poll()  # should not raise or block
