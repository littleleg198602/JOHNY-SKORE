"""Exercise real Git checkout with Windows-style newline conversion enabled."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import os
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "market_checker_app/data/market_checker_687_tickers.csv",
    "market_checker_app/data/verified_13f_securities.json",
    "tests/fixtures/healthcare/reviewed_seed_identities_20261011.json",
    "evidence/batch3_healthcare_20261012/captures/fda_crl_abbvie.body.json",
    "evidence/batch3_sec_20261012/captures.json",
    "evidence/batch3_identity_bulk_20261012/snapshots/bf6d2cd21d238ba21ebef7a18824e71f40b269fa5961767dc0721513de213179.bin",
)


class GitSourceByteIntegrityTests(unittest.TestCase):
    def test_checkout_preserves_real_source_bytes_with_autocrlf_enabled(self):
        environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

        def git(directory, *arguments):
            return subprocess.run(
                ["git", *arguments], cwd=directory, env=environment,
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

        with TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            checkout = Path(temporary) / "checkout"
            repository.mkdir()
            git(repository, "-c", "init.defaultBranch=main", "init")
            git(repository, "config", "core.autocrlf", "true")
            (repository / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
            originals = {name: (ROOT / name).read_bytes() for name in FILES}
            # Raw publisher bytes can contain mixed endings and binary values.
            originals["evidence/source-byte-probe.response"] = b"LF\nCRLF\r\n\x00\xff"
            originals["evidence/source-crlf-probe.json"] = b'{\r\n  "publisher": "synthetic-byte-probe"\r\n}\r\n'
            for name, payload in originals.items():
                destination = repository / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            git(repository, "add", ".")
            git(repository, "-c", "user.name=Byte integrity fixture", "-c",
                "user.email=byte-integrity@example.invalid", "commit", "-m", "Temporary byte fixture")
            git(repository, "clone", "--no-local", "--config", "core.autocrlf=true",
                str(repository), str(checkout))
            self.assertEqual(b"true", git(checkout, "config", "core.autocrlf").stdout.strip())
            for name, original in originals.items():
                with self.subTest(path=name):
                    self.assertEqual(hashlib.sha256(original).hexdigest(),
                                     hashlib.sha256((checkout / name).read_bytes()).hexdigest())

