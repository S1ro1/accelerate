# Copyright 2025 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import gc
import json
import os
import threading
import time

import psutil
import torch

from accelerate import Accelerator


class MemoryTracker:
    def __init__(
        self,
        device: torch.device,
        output_directory: str,
        run_name: str,
        save_memory_snapshot: bool,
        log_interval: float = 0.01,
    ):
        """Class for tracking gpu and cpu memory usage of the process.

        Args:
            device (torch.device): Cuda device to monitor.
            output_directory (str): Directory to save the memory usage data to, will be created if it doesn't exist.
            run_name (str): Name of the run, will be used to name the output files.
            save_memory_snapshot (bool): Whether to also save `torch.cuda.memory._dump_snapshot` to the output directory.
            log_interval (float, optional): Interval in seconds between memory measurements. Defaults to 0.01.
        """
        self.log_interval = log_interval
        self.save_memory_snapshot = save_memory_snapshot
        self.output_directory = output_directory
        self.run_name = run_name

        self.timestamps = []
        self.allocated_memory = []
        self.reserved_memory = []
        self.virtual_memory = []

        self.start_time = None
        self.running = False

        self._thread = None
        self._accelerator = Accelerator()
        self._process = psutil.Process()
        self._devicee = device

    def _monitor(self):
        self.start_time = time.time()

        while self.running:
            allocated = torch.cuda.memory_allocated(self._devicee) / (1024 * 1024)
            reserved = torch.cuda.memory_reserved(self._devicee) / (1024 * 1024)
            virtual_memory = self._process.memory_info().rss / (1024 * 1024)

            self.allocated_memory.append(allocated)
            self.reserved_memory.append(reserved)
            self.virtual_memory.append(virtual_memory)
            self.timestamps.append(time.time() - self.start_time)

            time.sleep(self.log_interval)

    def _pad_tensor_to_power_of_2(self, tensor):
        length = tensor.size(0)
        next_power_of_2 = 2 ** (length - 1).bit_length()
        if length != next_power_of_2:
            padding = -torch.ones(next_power_of_2 - length, dtype=tensor.dtype, device=tensor.device)
            tensor = torch.cat([tensor, padding])
        return tensor

    def start(self):
        gc.collect()
        torch.cuda.empty_cache()

        os.makedirs(self.output_directory, exist_ok=True)

        if self.save_memory_snapshot:
            torch.cuda.memory._record_memory_history()

        self.running = True
        self._thread = threading.Thread(target=self._monitor)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join()

        if self.save_memory_snapshot and self._accelerator.is_main_process:
            output_file = os.path.join(self.output_directory, f"{self.run_name}_memory_snapshot.pkl")
            torch.cuda.memory._dump_snapshot(output_file)

        if self._accelerator.is_main_process:
            path = os.path.join(self.output_directory, f"{self.run_name}_memory_usage.json")
            with open(path, "w") as f:
                json.dump(
                    {
                        "timestamps": self.timestamps,
                        "allocated_memory": self.allocated_memory,
                        "reserved_memory": self.reserved_memory,
                        "virtual_memory": self.virtual_memory,
                    },
                    f,
                )

    @property
    def peak_allocated_memory(self):
        return max(self.allocated_memory)

    @property
    def peak_reserved_memory(self):
        return max(self.reserved_memory)
