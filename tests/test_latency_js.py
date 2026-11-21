import json
from pathlib import Path
import shutil
import subprocess
import unittest


LATENCY_JS = Path(__file__).resolve().parents[1] / "src" / "codejam16" / "web" / "static" / "latency.js"
SCRIPT = """
const latency = require(process.argv[1]);
const firstSeen = new Map();
const check = (id, source, detected) => ({ id, source, detected_at: detected });
const snapshot = (checks) => ({ server_time: '2026-11-21T22:00:00.800Z', checks });
latency.measureNewChecks(firstSeen, snapshot([check('old', 'live', '2026-11-21T21:58:00.000Z'),
                                              check('sample', 'rehearsal', '2026-11-21T21:58:00.000Z')]),
                         1000, 1400, true);
latency.measureNewChecks(firstSeen, snapshot([check('old', 'live', '2026-11-21T21:58:00.000Z'),
                                              check('new', 'live', '2026-11-21T22:00:00.000Z')]),
                         1000, 1400, false);
console.log(JSON.stringify(Object.fromEntries(firstSeen)));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class DetectionToScreenTests(unittest.TestCase):
    def measured(self):
        output = subprocess.run(["node", "-e", SCRIPT, str(LATENCY_JS)], check=True,
                                capture_output=True, text=True).stdout
        return json.loads(output)

    def test_checks_present_at_page_load_are_not_measured(self):
        measured = self.measured()
        self.assertIsNone(measured["old"])
        self.assertNotIn("sample", measured)

    def test_new_check_uses_server_clock_plus_half_round_trip(self):
        self.assertAlmostEqual(self.measured()["new"], 0.8 + 0.2)


if __name__ == "__main__":
    unittest.main()
