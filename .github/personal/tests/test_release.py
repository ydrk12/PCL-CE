import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

PERSONAL = Path(__file__).resolve().parents[1]
TEST_TEMP = PERSONAL.parents[1] / "work/personal-tests"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("personal_release", PERSONAL / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
SIGNATURE = bytes.fromhex("8b1202b96a612038727b930214d7a03213f5b9e6efae3318ee3b2dce24b36aae")


def string(value):
    data = value.encode()
    length = len(data)
    prefix = bytearray()
    while length >= 128:
        prefix.append((length & 127) | 128)
        length >>= 7
    prefix.append(length)
    return prefix + data


def bundle(assembly, compressed=0):
    offset = 256
    header = offset + len(assembly)
    result = bytearray(offset)
    struct.pack_into("<q", result, 8, header)
    result[16:48] = SIGNATURE
    result.extend(assembly)
    result.extend(struct.pack("<IIi", 6, 0, 1) + string("fixture") + bytes(40))
    result.extend(struct.pack("<qqqB", offset, len(assembly), compressed, 1) + string("Plain Craft Launcher 2.dll"))
    return result


class DiscoveryTests(unittest.TestCase):
    def item(self, tag, date="2026-10-21T00:00:00Z", draft=False, prerelease=False):
        return dict(tag_name=tag, published_at=date, draft=draft, prerelease=prerelease)

    def test_only_unpublished_stable_releases_are_selected_in_order(self):
        upstream = [self.item("v2.17.0"), self.item("v2.16.0", "2026-10-20T23:00:00Z"),
                    self.item("v2.18.0-beta.1", prerelease=True), self.item("v2.18.0", draft=True),
                    self.item("v2.14.0", "2026-09-01T00:00:00Z")]
        self.assertEqual(release.candidates(upstream, []), ["v2.16.0", "v2.17.0"])

    def test_draft_retries_and_published_releases_are_idempotent(self):
        upstream = [self.item("v2.15.1"), self.item("v2.16.0")]
        existing = [self.item("v2.15.1-regionfree.1"), self.item("v2.16.0-regionfree.1", draft=True)]
        self.assertEqual(release.candidates(upstream, existing), ["v2.16.0"])

    def test_untrusted_tags_are_rejected(self):
        for tag in ("../dev", "v1;echo bad", "-v1", "v1\nmalicious", "v1/other"):
            with self.assertRaises(ValueError):
                release.valid_tag(tag)


class BinaryPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        process = subprocess.run(["dotnet", "build", str(PERSONAL / "tests/Fixture"), "-c", "Release"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if process.returncode:
            raise RuntimeError(process.stdout + process.stderr)
        cls.fixture = PERSONAL / "tests/Fixture/bin/Release/net8.0"
        cls.assembly = (cls.fixture / "Plain Craft Launcher 2.dll").read_bytes()
        cls.tool = PERSONAL / "RegionPatch/bin/Release/net8.0/RegionPatch.dll"

    def run_patcher(self, source, output, report):
        return subprocess.run(["dotnet", str(self.tool), str(source), str(output), str(report)], capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_return_values_and_throw_wrapper_execute_correctly(self):
        with tempfile.TemporaryDirectory(prefix="pcl-test-", dir=TEST_TEMP) as temporary:
            directory = Path(temporary)
            source, output, report = [directory / n for n in ("original.exe", "patched.exe", "report.json")]
            source.write_bytes(bundle(self.assembly))
            result = self.run_patcher(source, output, report)
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = json.loads(report.read_text())
            self.assertTrue(metadata["outsideApprovedIlIdentical"])
            self.assertEqual(len(metadata["methods"]), 2)
            modified = output.read_bytes()
            for index, (old, new) in enumerate(zip(source.read_bytes(), modified)):
                if old != new:
                    self.assertTrue(any(r["Offset"] <= index < r["Offset"] + r["Length"] for r in metadata["methods"]))
            dll = directory / "Plain Craft Launcher 2.dll"
            dll.write_bytes(modified[256:256 + len(self.assembly)])
            shutil.copyfile(self.fixture / "Plain Craft Launcher 2.runtimeconfig.json", directory / "Plain Craft Launcher 2.runtimeconfig.json")
            behavior = subprocess.run(["dotnet", str(dll)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(behavior.returncode, 0, behavior.stderr)
            self.assertEqual(json.loads(behavior.stdout), dict(restricted=False, allowed=True, threw=False))
            # Already-patched inputs must not be silently accepted as new official inputs.
            self.assertNotEqual(self.run_patcher(output, directory / "again.exe", directory / "again.json").returncode, 0)

    def test_malformed_and_compressed_inputs_never_create_output(self):
        with tempfile.TemporaryDirectory(prefix="pcl-test-", dir=TEST_TEMP) as temporary:
            directory = Path(temporary)
            for data in (b"not a bundle", bundle(self.assembly, compressed=1)):
                source = directory / "input.exe"
                source.write_bytes(data)
                output = directory / "output.exe"
                result = self.run_patcher(source, output, directory / "report.json")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
