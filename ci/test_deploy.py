import unittest
from pathlib import Path


class DeploymentTests(unittest.TestCase):
    def test_script_has_its_own_input_file(self):
        workflow = (Path(__file__).resolve().parent.parent / ".github/workflows/ci-cd.yml").read_text()
        self.assertNotIn("| bash -s", workflow)
        self.assertIn("bash /opt/everpush/ci/deploy.sh", workflow)
