"""Serializes every blocking call to the ELM327 onto one background thread,
so the Tkinter main loop never freezes waiting on serial I/O, and so two
actions (say, the live-data loop and a DTC read) never talk to the adapter
at the same time - ELM327's AT/OBD protocol is strictly half-duplex
request/response, and interleaving two requests would corrupt both.

Uses a pull model for results rather than having the worker thread call
back into Tkinter directly: Tk widget methods (including `Widget.after`)
aren't reliably safe to call from a non-main thread unless the Tcl
interpreter considers itself "in the main loop", which isn't guaranteed
just because `mainloop()` was started - so the worker thread only ever
touches its own queues, and the caller drains results by calling `poll()`
from a `root.after(...)`-scheduled callback on the main thread.

Deliberately has no Tkinter import, so it's unit-testable on its own (see
tests/test_io_worker.py).
"""
from __future__ import annotations

import queue
import threading
from typing import Callable

_STOP = object()


class IOWorker:
    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="vagscan-io-worker")
        self._thread.start()

    def submit(self, job: Callable[[], object], on_done: Callable[[bool, object], None] | None = None) -> None:
        """Run `job()` on the IO thread. Once it finishes (or raises), the
        `(on_done, ok, result)` triple is queued for `poll()` to deliver."""
        self._jobs.put((job, on_done))

    def poll(self) -> None:
        """Call periodically from the UI thread (e.g. via `root.after`).
        Invokes `on_done(ok, result)` for every job that finished since the
        last call, in completion order."""
        while True:
            try:
                on_done, ok, result = self._results.get_nowait()
            except queue.Empty:
                return
            if on_done is not None:
                on_done(ok, result)

    def stop(self) -> None:
        self._jobs.put((_STOP, None))

    def _run(self) -> None:
        while True:
            job, on_done = self._jobs.get()
            if job is _STOP:
                return
            try:
                result: object = job()
            except Exception as exc:  # noqa: BLE001 - deliberately broad, forwarded to the caller
                ok, result = False, exc
            else:
                ok = True
            self._results.put((on_done, ok, result))
