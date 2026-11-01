"""Public login assets must load on replicas with no dashboard/WebSocket session."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from modules.component_assets import sign_in_component_path


class HostedStartupTests(unittest.TestCase):
    def test_each_fresh_replica_serves_google_component_before_any_dashboard_runs(self):
        root = Path(__file__).resolve().parents[1]
        for replica in range(2):
            with self.subTest(replica=replica):
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                process = subprocess.Popen(
                    [sys.executable, str(root / "hosted.py")], cwd=root,
                    env={**os.environ, "PORT": str(port)},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                try:
                    origin = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            with urlopen(origin + "/_stcore/health", timeout=1) as response:
                                self.assertEqual(response.status, 200)
                            break
                        except URLError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail("Hosted Streamlit process did not start")
                            time.sleep(0.05)
                    for asset, mime in (("index.html", "text/html"), ("sign-in.js", "application/javascript")):
                        path = "/component/modules.auth.sensemaker_google_sign_in/" + asset
                        with urlopen(origin + path, timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            self.assertIn(mime, response.headers["Content-Type"])
                            data = response.read()
                            self.assertEqual(data, (Path(sign_in_component_path()) / asset).read_bytes())
                            if asset == "index.html":
                                self.assertNotIn(b'src="./sign-in.js', data)
                                self.assertIn(b'streamlit:componentReady', data)
                finally:
                    process.terminate()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
