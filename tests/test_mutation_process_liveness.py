import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("liveness_subject", ROOT / "scripts/personal_ai_sync.py")
subject = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = subject
spec.loader.exec_module(subject)


class ProcessLivenessTests(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(subject._pid_is_alive(os.getpid()))

    @unittest.skipUnless(os.name == "nt", "Windows process object lifetime")
    def test_exited_process_with_retained_handle_is_not_an_active_writer(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read(1)"], stdin=subprocess.PIPE)
        handle = kernel.OpenProcess(0x00100000, 0, proc.pid)
        self.assertTrue(handle)
        try:
            self.assertTrue(subject._pid_is_alive(proc.pid))
            proc.communicate(b"x", timeout=10)
            self.assertEqual(proc.returncode, 0)
            self.assertFalse(subject._pid_is_alive(proc.pid))
        finally:
            kernel.CloseHandle(handle)
            if proc.poll() is None:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()
