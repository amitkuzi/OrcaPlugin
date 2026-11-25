# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_release


class Arm64Packaging(unittest.TestCase):
    def test_candidate_and_generic_keep_native_loader_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generic = build_release.build(root / "generic")
            arm = build_release.build(root / "arm", "windows-arm64")
            self.assertNotIn("arm64", generic.name)
            self.assertTrue(arm.name.endswith("-windows-arm64-validation-candidate.zip"))
            self.assertIn(f"{hashlib.sha256(arm.read_bytes()).hexdigest()}  {arm.name}",
                          (arm.parent / "SHA256SUMS.txt").read_text())
            with zipfile.ZipFile(generic) as stable, zipfile.ZipFile(arm) as candidate:
                self.assertEqual(set(candidate.namelist()) - set(stable.namelist()),
                                 {"WINDOWS-ARM64.md"})
                manifest = json.loads(candidate.read("release.json"))
                self.assertEqual(manifest["target_platform"], "windows-arm64")
                self.assertEqual(manifest["distribution_status"], "validation-candidate")
                for flag in ("native_arm64_verified", "orca_slicer_ui_validated",
                             "physical_printer_validated", "architecture_specific_binary"):
                    self.assertIs(manifest[flag], False)
                plugin = manifest["installable_filename"]
                self.assertEqual(plugin, "centauri_canvas_sync.py")
                self.assertEqual(candidate.read(plugin), stable.read(plugin))
                for name, digest in manifest["zip_payload_sha256"].items():
                    self.assertEqual(hashlib.sha256(candidate.read(name)).hexdigest(), digest)
            self.assertEqual(arm.read_bytes(),
                             build_release.build(root / "repeat", "windows-arm64").read_bytes())

    def test_unknown_target_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"
            with self.assertRaises(ValueError):
                build_release.build(dest, "unrecognized")
            self.assertFalse(dest.exists())
