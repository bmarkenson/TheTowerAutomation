"""Continuously drained Android H.264 frames for low-latency decisions."""

from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Deque, Optional, Tuple

import cv2
import numpy as np

from core.adb_utils import ADB_DEVICE_ID


Frame = np.ndarray


class ScreenrecordFrameStream:
    """Keep only the newest frame from a persistent Android video stream."""

    def __init__(
        self,
        *,
        device_id: Optional[str] = None,
        bit_rate: int = 3_000_000,
        size: Tuple[int, int] = (1080, 1920),
    ) -> None:
        self._device_id = device_id or os.getenv("ADB_DEVICE") or ADB_DEVICE_ID
        self._bit_rate = max(500_000, int(bit_rate))
        self._size = size
        self._fifo_path = Path(tempfile.gettempdir()) / (
            f"thetower-screenrecord-{os.getpid()}-{id(self)}.h264"
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._live = threading.Event()
        self._failed = threading.Event()
        self._latest_frame: Optional[Frame] = None
        self._latest_sequence = 0
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._producer_thread: Optional[threading.Thread] = None
        self._consumer_thread: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None

    @property
    def is_live(self) -> bool:
        return self._live.is_set()

    @property
    def failed(self) -> bool:
        return self._failed.is_set()

    @property
    def age_s(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def start(self) -> None:
        if self._started_at is not None:
            return
        os.mkfifo(self._fifo_path)
        self._started_at = time.monotonic()
        self._producer_thread = threading.Thread(
            target=self._produce,
            name="thetower-screenrecord-producer",
            daemon=True,
        )
        self._consumer_thread = threading.Thread(
            target=self._consume,
            name="thetower-screenrecord-consumer",
            daemon=True,
        )
        self._producer_thread.start()
        self._consumer_thread.start()

    def latest_frame(self) -> Tuple[int, Optional[Frame]]:
        """Return the latest immutable frame reference and its sequence."""

        with self._lock:
            return self._latest_sequence, self._latest_frame

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)

        for thread in (self._producer_thread, self._consumer_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)

        try:
            self._fifo_path.unlink()
        except FileNotFoundError:
            pass

    def _produce(self) -> None:
        try:
            fifo_fd = os.open(self._fifo_path, os.O_WRONLY)
            try:
                command = ["adb"]
                if self._device_id:
                    command.extend(["-s", self._device_id])
                command.extend(
                    [
                        "exec-out",
                        "screenrecord",
                        "--output-format=h264",
                        "--bit-rate",
                        str(self._bit_rate),
                        "--size",
                        f"{self._size[0]}x{self._size[1]}",
                        "-",
                    ]
                )
                self._process = subprocess.Popen(
                    command,
                    stdout=fifo_fd,
                    stderr=subprocess.DEVNULL,
                )
                return_code = self._process.wait()
                if return_code != 0 and not self._stop.is_set():
                    self._failed.set()
            finally:
                os.close(fifo_fd)
        except Exception:
            if not self._stop.is_set():
                self._failed.set()

    def _consume(self) -> None:
        read_durations: Deque[float] = deque(maxlen=8)
        capture = cv2.VideoCapture(str(self._fifo_path), cv2.CAP_FFMPEG)
        if not capture.isOpened():
            self._failed.set()
            return

        try:
            while not self._stop.is_set():
                started = time.monotonic()
                ok, frame = capture.read()
                elapsed = time.monotonic() - started
                if not ok:
                    if not self._stop.is_set():
                        self._failed.set()
                    return

                with self._lock:
                    self._latest_sequence += 1
                    self._latest_frame = frame

                # Opening the raw H.264 stream buffers several seconds while
                # probing. Backlogged frames decode in ~2-3 ms; live frames
                # block near the encoder cadence. Do not expose frames for
                # decisions until the decoder has demonstrably caught up.
                read_durations.append(elapsed)
                if (
                    len(read_durations) == read_durations.maxlen
                    and sum(value >= 0.012 for value in read_durations) >= 5
                ):
                    self._live.set()
        finally:
            capture.release()


__all__ = ["ScreenrecordFrameStream"]
