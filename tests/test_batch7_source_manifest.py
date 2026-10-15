"""Explicit file configuration only; deterministic fixtures are not acceptance."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services import prospective_source_manifest_service as module
from market_checker_app.services.prospective_source_manifest_service import (
    MANIFEST_FILENAME, MANIFEST_SCHEMA_VERSION, VERSIONS_DIRECTORY, ProspectiveSourceManifestService,
)
from market_checker_app.services.specialist_label_availability_service import persist_label_observation
from market_checker_app.services.yahoo_price_provenance_service import capture_provider_frame, persist_observation
from tests.test_specialist_evaluation_service import _label_observation, _snapshot


NOW = datetime(2026, 10, 15, 3, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
RSS = ROOT / "evidence/batch5_coordinator_20261014/source_runs/rss/specialist_source_run_9947758362370d6e8dac1763eb956ae3ddab0555844a507a213e26d95aadfd27_47ed52005d8796380d2472d593f0d74ea72c63da085ca0b7d23f78da124bd151.json"


def price(root, ticker="AAPL", *, value=100.0, observed=None, market_day="2026-10-12"):
    observed = observed or NOW - timedelta(minutes=1)
    frame = pd.DataFrame({"Close": [value]}, index=pd.to_datetime([market_day], utc=True))
    record = capture_provider_frame(ticker, frame, started_at=observed-timedelta(seconds=1), observed_at=observed)
    return persist_observation(record, Path(root) / "selected_sources")


def entry(root, paths, *, ticker="AAPL", benchmark="SPY", snapshot_id=None):
    result = {"ticker": ticker, "benchmark_ticker": benchmark,
              "source_archive_paths": [path.relative_to(root).as_posix() for path in paths]}
    if snapshot_id is not None:
        result["snapshot_id"] = snapshot_id
    return result


def manifest_bytes(prepared):
    return module._encoded(prepared["manifest"])


def label(root, *, captured=None, ticker="AAPL"):
    snapshot = _snapshot()
    snapshot["ticker"] = ticker
    record = _label_observation(snapshot)
    if captured is not None:
        record["captured_at"] = captured.isoformat()
    record["observation_hash"] = canonical_hash({key:value for key,value in record.items() if key != "observation_hash"})
    path = persist_label_observation(record, Path(root) / "selected_labels")
    return record, path


def root_aliases(canonical, temporary_name):
    aliases = [("temporary-spelling", Path(temporary_name)),
               ("parent-roundtrip", canonical / ".." / canonical.name)]
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        short_path.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        short_path.restype = wintypes.DWORD
        size = short_path(str(canonical), None, 0)
        if size == 0:
            # Short-name discovery is optional; the distinct non-reparse alias
            # still proves canonical guard targeting. Actual Windows junctions
            # are exercised separately, with failures rather than skips.
            aliases.append(("windows-short-unavailable-parent-roundtrip", canonical / ".." / canonical.name))
            return aliases
        if size > 32_768:
            raise AssertionError("Native short-path lookup exceeded the fixture path bound")
        buffer = ctypes.create_unicode_buffer(size)
        written = short_path(str(canonical), buffer, size)
        if not 0 < written < size:
            raise AssertionError("Native short-path lookup returned an incomplete fixture path")
        # Volumes without 8.3 names may return the long spelling. The distinct
        # parent-roundtrip alias is still exercised without skipping a test.
        short_alias=Path(buffer.value)
        label="windows-short-spelling" if short_alias!=canonical else "windows-no-distinct-short-spelling"
        aliases.append((label, short_alias))
    return aliases


class SourceManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # The service canonicalizes its root. Windows TemporaryDirectory may
        # return RUNNER~1 while resolve() returns the long profile name.
        # Create every fixture path and reparse target in that same namespace.
        self.root = Path(self.temporary.name).resolve()
        self.asset = price(self.root)
        self.benchmark = price(self.root,"SPY")
        self.service = ProspectiveSourceManifestService(self.root, observation_clock=lambda:NOW)
        self.row = entry(self.root,[self.asset,self.benchmark])

    def prepare(self, rows=None, labels=None):
        return self.service.prepare(forecast_sources=[self.row] if rows is None else rows, label_observations=labels)

    def assertNoPublication(self):
        self.assertFalse((self.root/MANIFEST_FILENAME).exists())
        self.assertFalse((self.root/VERSIONS_DIRECTORY).exists())
        self.assertFalse((self.root/"prospective_source_manifest.lock").exists())

    def test_prepare_uses_exact_selected_paths_without_glob_http_db_or_source_mutation(self):
        before = {path:path.read_bytes() for path in (self.asset,self.benchmark)}
        unselected=self.root/("yahoo_price_"+"f"*64+".json")
        unselected.write_text('{"access_token":"synthetic-fixture-only"}')
        with patch.object(Path,"glob",side_effect=AssertionError("No discovery")), \
                patch.object(Path,"rglob",side_effect=AssertionError("No discovery")), \
                patch("socket.socket.connect",side_effect=AssertionError("No HTTP")), \
                patch("sqlite3.connect",side_effect=AssertionError("No DB")):
            prepared=self.prepare()
        self.assertEqual({"schema_version","forecast_sources","label_observations"},set(prepared["manifest"]))
        self.assertEqual(MANIFEST_SCHEMA_VERSION,prepared["manifest"]["schema_version"])
        self.assertEqual([self.row],prepared["manifest"]["forecast_sources"])
        self.assertEqual([],prepared["manifest"]["label_observations"])
        self.assertEqual(before,{path:path.read_bytes() for path in before})
        self.assertEqual({"UNKNOWN"},{pin["reference"]["publisher_revision_timing_status"] for pin in prepared["source_pins"]})
        self.assertFalse(prepared["feature_source_coverage_verified"])
        self.assertEqual(0,prepared["forecasts_created"])
        self.assertEqual(0,prepared["labels_created"])
        self.assertNoPublication()

    def test_default_publish_only_prepares_an_atomic_content_addressed_version(self):
        prepared=self.prepare();result=self.service.publish(prepared)
        self.assertEqual("VERSION_PREPARED",result["status"])
        self.assertEqual(manifest_bytes(prepared),(self.root/result["version_path"]).read_bytes())
        self.assertFalse((self.root/MANIFEST_FILENAME).exists())
        self.assertFalse(result["active_manifest_changed"])

    def test_initial_explicit_activation_keeps_a_separate_immutable_inode(self):
        prepared=self.prepare();result=self.service.publish(prepared,activate=True)
        active=self.root/MANIFEST_FILENAME;version=self.root/result["version_path"]
        self.assertEqual("ACTIVATED",result["status"])
        self.assertEqual(active.read_bytes(),version.read_bytes())
        self.assertFalse(active.samefile(version))
        self.assertEqual(result["manifest_sha256"],self.service.current_manifest_sha256())
        active.write_bytes(module._encoded({"schema_version":MANIFEST_SCHEMA_VERSION,"forecast_sources":[],"label_observations":[]}))
        self.assertEqual(manifest_bytes(prepared),version.read_bytes())

    def test_expected_current_sha_replacement_preserves_both_original_versions(self):
        first=self.prepare();saved=self.service.publish(first,activate=True)
        other=price(self.root,value=102.0)
        second=self.prepare([entry(self.root,[other,self.benchmark])])
        result=self.service.publish(second,activate=True,expected_current_sha256=saved["manifest_sha256"])
        self.assertEqual("ACTIVATED",result["status"])
        self.assertEqual(manifest_bytes(second),(self.root/MANIFEST_FILENAME).read_bytes())
        self.assertEqual(manifest_bytes(first),(self.root/saved["version_path"]).read_bytes())
        self.assertEqual(manifest_bytes(second),(self.root/result["version_path"]).read_bytes())

    def test_existing_active_requires_explicit_matching_current_sha_even_for_stale_preparation(self):
        first=self.prepare();saved=self.service.publish(first,activate=True)
        other=price(self.root,value=102.0);second=self.prepare([entry(self.root,[other])])
        for expected in (None,"f"*64):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError,"COMPARE_AND_SWAP"):
                    self.service.publish(second,activate=True,expected_current_sha256=expected)
        self.assertEqual(manifest_bytes(first),(self.root/MANIFEST_FILENAME).read_bytes())
        version=self.root/VERSIONS_DIRECTORY/("prospective_source_manifest_"+second["manifest_sha256"]+".json")
        self.assertFalse(version.exists())
        replay=self.service.publish(first,activate=True,expected_current_sha256=saved["manifest_sha256"])
        self.assertEqual("ACTIVE_ALREADY_MATCHES",replay["status"])
        self.assertFalse(replay["active_manifest_changed"])

    def test_prior_empty_adapter_configuration_preserves_its_exact_noncanonical_raw_bytes(self):
        raw=b'{"schema_version":"prospective_forecast_source_manifest_v1","forecast_sources":[],"label_observations":[]}\r\n'
        (self.root/MANIFEST_FILENAME).write_bytes(raw)
        previous=hashlib.sha256(raw).hexdigest()
        self.service.publish(self.prepare(),activate=True,expected_current_sha256=previous)
        old=self.root/VERSIONS_DIRECTORY/("prospective_source_manifest_"+previous+".json")
        self.assertEqual(raw,old.read_bytes())

    def test_concurrent_initial_writers_cannot_silently_clobber_each_other(self):
        second_path=price(self.root,value=102.0)
        prepared=[self.prepare(),self.prepare([entry(self.root,[second_path])])]
        barrier=threading.Barrier(2)
        def publish(item):
            barrier.wait()
            try:return self.service.publish(item,activate=True)
            except ValueError as error:return {"error":str(error)}
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(publish,prepared))
        self.assertEqual(1,sum(result.get("status")=="ACTIVATED" for result in results))
        self.assertEqual(1,sum(result.get("error") in {"SOURCE_MANIFEST_PUBLICATION_BUSY","ACTIVE_SOURCE_MANIFEST_COMPARE_AND_SWAP_FAILED"} for result in results))
        self.assertIn((self.root/MANIFEST_FILENAME).read_bytes(),[manifest_bytes(item) for item in prepared])

    def test_mid_publication_external_change_is_detected_before_active_replace(self):
        first=self.prepare();saved=self.service.publish(first,activate=True)
        second_path=price(self.root,value=102.0);second=self.prepare([entry(self.root,[second_path])])
        external=module._encoded({"schema_version":MANIFEST_SCHEMA_VERSION,"forecast_sources":[],"label_observations":[]})
        original=module._immutable_version;changed=False
        def archive(root,raw):
            nonlocal changed
            result=original(root,raw)
            if not changed:
                changed=True;(self.root/MANIFEST_FILENAME).write_bytes(external)
            return result
        with patch.object(module,"_immutable_version",side_effect=archive):
            with self.assertRaisesRegex(ValueError,"COMPARE_AND_SWAP"):
                self.service.publish(second,activate=True,expected_current_sha256=saved["manifest_sha256"])
        self.assertEqual(external,(self.root/MANIFEST_FILENAME).read_bytes())
        self.assertEqual(manifest_bytes(first),(self.root/saved["version_path"]).read_bytes())

    def test_atomic_replace_failure_preserves_prior_active_and_recoverable_new_version(self):
        first=self.prepare();saved=self.service.publish(first,activate=True)
        second_path=price(self.root,value=102.0);second=self.prepare([entry(self.root,[second_path])])
        with patch.object(module.os,"replace",side_effect=OSError("deterministic write failure")):
            with self.assertRaises(OSError):self.service.publish(second,activate=True,expected_current_sha256=saved["manifest_sha256"])
        self.assertEqual(manifest_bytes(first),(self.root/MANIFEST_FILENAME).read_bytes())
        self.assertFalse((self.root/"prospective_source_manifest.lock").exists())
        self.assertFalse(any(path.suffix==".tmp" for path in self.root.iterdir()))
        retried=self.service.publish(second,activate=True,expected_current_sha256=saved["manifest_sha256"])
        self.assertEqual(manifest_bytes(second),(self.root/retried["version_path"]).read_bytes())

    def test_immutable_version_conflict_is_never_overwritten(self):
        prepared=self.prepare();saved=self.service.publish(prepared)
        version=self.root/saved["version_path"];version.write_text('{"wrong":true}')
        with self.assertRaisesRegex(ValueError,"IMMUTABLE"):
            self.service.publish(prepared,activate=True)
        self.assertEqual('{"wrong":true}',version.read_text())
        self.assertFalse((self.root/MANIFEST_FILENAME).exists())

    def test_source_change_after_preparation_is_revalidated_before_any_write(self):
        prepared=self.prepare();self.asset.write_text('{"invalid":true}')
        with self.assertRaises(ValueError):self.service.publish(prepared,activate=True)
        self.assertNoPublication()

    def test_future_source_capture_cannot_be_backdated_to_its_old_market_session(self):
        later=price(self.root,observed=NOW+timedelta(seconds=1),market_day="2000-01-03")
        with self.assertRaisesRegex(ValueError,"AVAILABLE_AFTER_PREDICTION"):
            self.prepare([entry(self.root,[later])])
        self.assertNoPublication()

    def test_publish_rejects_a_clock_before_physical_preparation(self):
        prepared=self.prepare();self.service._clock=lambda:NOW-timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError,"PHYSICAL_CLOCK_REGRESSION"):
            self.service.publish(prepared,activate=True)
        restarted=ProspectiveSourceManifestService(self.root,observation_clock=lambda:NOW-timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError,"PREPARATION_FROM_FUTURE"):
            restarted.publish(prepared,activate=True)
        self.assertNoPublication()

    def test_publish_revalidation_cannot_roll_back_its_shared_physical_highwater(self):
        prepared=self.prepare()
        times=iter([NOW+timedelta(hours=2),NOW+timedelta(hours=1)])
        self.service._clock=lambda:next(times)
        with self.assertRaisesRegex(ValueError,"PHYSICAL_CLOCK_REGRESSION"):
            self.service.publish(prepared,activate=True)
        self.assertNoPublication()

    def test_preparation_cannot_use_an_earlier_clock_after_its_source_reads(self):
        times=iter([NOW+timedelta(hours=2),NOW+timedelta(hours=1)])
        service=ProspectiveSourceManifestService(self.root,observation_clock=lambda:next(times))
        with self.assertRaisesRegex(ValueError,"PHYSICAL_CLOCK_REGRESSION"):
            service.prepare(forecast_sources=[self.row])
        self.assertNoPublication()

    def test_publication_does_not_sample_a_backdated_timestamp_after_os_replace(self):
        prepared=self.prepare();replaced=False;counter=0
        def clock():
            nonlocal counter
            if replaced:raise AssertionError("Clock sampled after active mutation")
            counter+=1
            return NOW+timedelta(seconds=counter)
        self.service._clock=clock
        original=module.os.replace
        def replace(before,after):
            nonlocal replaced
            original(before,after);replaced=True
        with patch.object(module.os,"replace",side_effect=replace):
            saved=self.service.publish(prepared,activate=True)
        self.assertTrue(replaced)
        self.assertGreaterEqual(datetime.fromisoformat(saved["published_at"]),datetime.fromisoformat(prepared["prepared_at"]))
        self.assertEqual("PHYSICAL_CHECK_BEFORE_ATOMIC_PUBLICATION",saved["publication_clock_basis"])

    def test_foreign_price_and_missing_or_foreign_ticker_are_rejected(self):
        foreign=price(self.root,"MSFT")
        with self.assertRaisesRegex(ValueError,"FOREIGN_PRICE"):
            self.prepare([entry(self.root,[foreign])])
        for value in (None,"NOT_REAL",[],""):
            with self.subTest(ticker=value):
                row=deepcopy(self.row);row["ticker"]=value
                with self.assertRaises(ValueError):self.prepare([row])
        self.assertNoPublication()

    def test_missing_benchmark_empty_sources_and_ambiguous_scopes_are_rejected(self):
        for mutate in (lambda row:row.pop("benchmark_ticker"),lambda row:row.update(benchmark_ticker=""),
                       lambda row:row.update(source_archive_paths=[])):
            row=deepcopy(self.row);mutate(row)
            with self.assertRaises(ValueError):self.prepare([row])
        scoped=deepcopy(self.row);scoped["snapshot_id"]="a"*64
        with self.assertRaisesRegex(ValueError,"OVERLAPPING"):
            self.prepare([self.row,scoped])
        with self.assertRaisesRegex(ValueError,"AMBIGUOUS"):
            self.prepare([self.row,self.row])
        self.assertNoPublication()

    def test_paths_must_be_explicit_relative_existing_files(self):
        for value in ("../"+self.asset.name,str(self.asset),"selected_sources/*.json","selected_sources//"+self.asset.name,
                      "selected_sources/../"+self.asset.name,"selected_sources\\"+self.asset.name,
                      "missing/"+self.asset.name):
            with self.subTest(path=value):
                row=deepcopy(self.row);row["source_archive_paths"]=[value]
                with self.assertRaises(ValueError):self.prepare([row])
        self.assertNoPublication()

    def test_selected_source_symlink_or_symlink_parent_is_refused_before_body_reads(self):
        original=Path.is_symlink
        for selected in (self.asset,self.asset.parent):
            with self.subTest(selected=selected):
                hits=[]
                def symlink(path):
                    if path==selected:
                        hits.append(path)
                        return True
                    return original(path)
                with patch.object(Path,"is_symlink",symlink):
                    with self.assertRaisesRegex(ValueError,"SYMLINK"):
                        self.prepare()
                self.assertIn(selected,hits,"The source reparse mock must reach the intended canonical path")
        self.assertNoPublication()

    def test_symlink_active_lock_and_version_directory_are_not_followed(self):
        prepared=self.prepare();original=Path.is_symlink
        for selected in (self.root/MANIFEST_FILENAME,self.root/"prospective_source_manifest.lock",self.root/VERSIONS_DIRECTORY):
            with self.subTest(selected=selected):
                hits=[]
                def symlink(path):
                    if path==selected:
                        hits.append(path)
                        return True
                    return original(path)
                with patch.object(Path,"is_symlink",symlink):
                    with self.assertRaisesRegex(ValueError,"SYMLINK"):
                        self.service.publish(prepared,activate=True)
                self.assertIn(selected,hits,"The publication reparse mock must reach the intended canonical path")
        self.assertNoPublication()

    def test_windows_junction_aliases_cannot_escape_source_or_version_roots(self):
        prepared=self.prepare()
        original=getattr(Path,"is_junction",lambda path:False)
        for selected in (self.asset.parent,self.root/VERSIONS_DIRECTORY):
            with self.subTest(selected=selected):
                hits=[]
                def junction(path):
                    if path==selected:
                        hits.append(path)
                        return True
                    return original(path)
                with patch.object(Path,"is_junction",junction,create=True):
                    with self.assertRaisesRegex(ValueError,"SYMLINK"):
                        self.service.publish(prepared,activate=True)
                self.assertIn(selected,hits,"The junction mock must reach the intended canonical path")
        self.assertNoPublication()

    def test_canonical_reparse_targets_work_after_posix_and_windows_root_alias_resolution(self):
        self.assertEqual(self.root,self.root.resolve())
        parent_alias=self.root/".."/self.root.name
        self.assertNotEqual(parent_alias,self.root)
        for spelling,alias in root_aliases(self.root,self.temporary.name):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.root,alias.resolve())
                service=ProspectiveSourceManifestService(alias,observation_clock=lambda:NOW)
                self.assertEqual(self.root,service.root)
                prepared=service.prepare(forecast_sources=[self.row])
                for kind in ("is_symlink","is_junction"):
                    original=getattr(Path,kind,lambda path:False)
                    for selected in (self.asset.parent,self.root/"prospective_source_manifest.lock",self.root/VERSIONS_DIRECTORY):
                        with self.subTest(kind=kind,selected=selected):
                            hits=[]
                            def reparse(path):
                                if path==selected:
                                    hits.append(path)
                                    return True
                                return original(path)
                            with patch.object(Path,kind,reparse,create=True):
                                with self.assertRaisesRegex(ValueError,"SYMLINK"):
                                    service.publish(prepared,activate=True)
                            self.assertIn(selected,hits)
                            self.assertNoPublication()

    def test_actual_directory_reparse_is_rejected_for_sources_and_publication(self):
        def make_reparse(link,target):
            if os.name=="nt":
                completed=subprocess.run([os.environ.get("COMSPEC","cmd.exe"),"/d","/c","mklink","/J",str(link),str(target)],
                    capture_output=True,text=True,timeout=10,check=False)
                self.assertEqual(0,completed.returncode,"Temporary junction creation failed: "+completed.stderr)
                self.assertTrue(link.is_junction())
            else:
                link.symlink_to(target,target_is_directory=True)
                self.assertTrue(link.is_symlink())
        def remove_reparse(link):
            if os.name=="nt":link.rmdir()
            else:link.unlink()
        before={path:path.read_bytes() for path in (self.asset,self.benchmark)}
        link=self.root/"reparse_sources"
        make_reparse(link,self.asset.parent)
        try:
            selected=deepcopy(self.row)
            selected["source_archive_paths"]=["reparse_sources/"+self.asset.name]
            with self.assertRaisesRegex(ValueError,"SYMLINK"):
                self.prepare([selected])
        finally:
            remove_reparse(link)
        self.assertNoPublication()
        prepared=self.prepare()
        destination=self.root/"version_destination";destination.mkdir()
        link=self.root/VERSIONS_DIRECTORY
        make_reparse(link,destination)
        try:
            with self.assertRaisesRegex(ValueError,"SYMLINK"):
                self.service.publish(prepared,activate=True)
            self.assertEqual([],list(destination.iterdir()))
        finally:
            remove_reparse(link)
        self.assertEqual(before,{path:path.read_bytes() for path in before})
        self.assertNoPublication()

    def test_byte_and_entry_budgets_fail_before_publication(self):
        with patch.object(module,"MAX_CAPTURE_BYTES",1):
            with self.assertRaisesRegex(ValueError,"BYTE_LIMIT"):self.prepare()
        with patch.object(module,"MAX_SELECTED_BYTES",self.asset.stat().st_size):
            with self.assertRaisesRegex(ValueError,"TOTAL_BYTE_LIMIT"):self.prepare()
        with self.assertRaises(ValueError):self.prepare([self.row]*688)
        row=deepcopy(self.row);row["source_archive_paths"]*=13
        with self.assertRaises(ValueError):self.prepare([row])
        self.assertNoPublication()

    def test_unsafe_or_duplicate_json_is_rejected_in_full_without_projection(self):
        original=self.asset.read_bytes()
        for raw,reason in [(b'{"api_key":"synthetic-fixture-only"}',"WITHHELD"),
                           (b'{"x":1,"x":2}',"DUPLICATE"),
                           (b'{"access_\\u0074oken":"synthetic-fixture-only"}',"WITHHELD")]:
            with self.subTest(reason=reason):
                self.asset.write_bytes(raw)
                with self.assertRaisesRegex(ValueError,reason):self.prepare()
        self.asset.write_bytes(original)
        row=deepcopy(self.row);row["password"]="synthetic-fixture-only"
        with self.assertRaisesRegex(ValueError,"WITHHELD"):self.prepare([row])
        self.assertNoPublication()

    def test_actual_specialist_archive_requires_recorded_ticker_scope_not_universe_membership(self):
        target=self.root/"selected_sources"/RSS.name;target.write_bytes(RSS.read_bytes())
        prepared=self.prepare([entry(self.root,[target],ticker="MSFT")])
        self.assertEqual("RECORDED_TICKER_REFERENCE_ONLY",prepared["forecast_source_scopes"][0]["sources"][0]["scope"])
        self.assertFalse(prepared["feature_source_coverage_verified"])
        with self.assertRaisesRegex(ValueError,"FOREIGN_OR_MISSING_SPECIALIST"):
            self.prepare([entry(self.root,[target],ticker="AAPL")])
        self.assertNoPublication()

    def test_specialist_universe_tamper_is_rejected_by_the_existing_archive_guard(self):
        record=json.loads(RSS.read_bytes());archive=record["source_archive"]
        archive["universe"]["ticker_count"]-=1
        archive["archive_hash"]=canonical_hash({key:value for key,value in archive.items() if key!="archive_hash"})
        record["snapshot_hash"]=canonical_hash({key:value for key,value in record.items() if key!="snapshot_hash"})
        path=self.root/"selected_sources"/f"specialist_source_run_{record['source_run_id']}_{record['snapshot_hash']}.json"
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,"687"):
            self.prepare([entry(self.root,[path],ticker="MSFT")])
        self.assertNoPublication()

    def test_existing_label_file_can_be_selected_without_creating_or_attaching_a_label(self):
        record,path=label(self.root)
        before=path.read_bytes()
        prepared=self.prepare(labels=[{"snapshot_id":record["snapshot_id"],"path":path.relative_to(self.root).as_posix()}])
        self.assertEqual(record["observation_hash"],prepared["label_pins"][0]["observation_hash"])
        self.assertEqual(0,prepared["labels_created"])
        self.service.publish(prepared,activate=True)
        self.assertEqual(before,path.read_bytes())
        self.assertFalse((self.root/"prospective_specialist_history").exists())

    def test_future_foreign_or_mismatched_label_is_rejected_before_writing(self):
        record,path=label(self.root,captured=NOW+timedelta(seconds=1))
        selected={"snapshot_id":record["snapshot_id"],"path":path.relative_to(self.root).as_posix()}
        with self.assertRaisesRegex(ValueError,"LABEL_CLOCKS"):self.prepare(labels=[selected])
        record,path=label(self.root,ticker="NOT_REAL")
        selected={"snapshot_id":record["snapshot_id"],"path":path.relative_to(self.root).as_posix()}
        with self.assertRaisesRegex(ValueError,"FOREIGN_LABEL"):self.prepare(labels=[selected])
        selected["snapshot_id"]="a"*64
        with self.assertRaisesRegex(ValueError,"IDENTITY_MISMATCH"):self.prepare(labels=[selected])
        self.assertNoPublication()

    def test_preparation_hash_root_and_selected_pins_cannot_be_silently_changed(self):
        prepared=self.prepare();prepared["manifest"]["forecast_sources"][0]["benchmark_ticker"]="QQQ"
        with self.assertRaisesRegex(ValueError,"INVALID_SOURCE_MANIFEST_PREPARATION"):
            self.service.publish(prepared,activate=True)
        prepared=self.prepare();prepared["source_pins"][0]["file_sha256"]="f"*64
        prepared["preparation_hash"]=canonical_hash({key:value for key,value in prepared.items() if key!="preparation_hash"})
        with self.assertRaisesRegex(ValueError,"CHANGED_AFTER_PREPARATION"):
            self.service.publish(prepared,activate=True)
        self.assertNoPublication()

    def test_existing_lock_is_not_deleted_or_treated_as_permission_to_overwrite(self):
        prepared=self.prepare();lock=self.root/"prospective_source_manifest.lock";lock.write_text("another writer")
        with self.assertRaisesRegex(ValueError,"PUBLICATION_BUSY"):
            self.service.publish(prepared,activate=True)
        self.assertEqual("another writer",lock.read_text())
        self.assertFalse((self.root/MANIFEST_FILENAME).exists())

    def test_missing_or_symlink_audit_root_is_rejected_without_creation(self):
        missing=self.root/"not_created"
        with self.assertRaisesRegex(ValueError,"ROOT_UNAVAILABLE"):
            ProspectiveSourceManifestService(missing)
        self.assertFalse(missing.exists())
        original=Path.is_symlink
        with patch.object(Path,"is_symlink",lambda path:path==self.root or original(path)):
            with self.assertRaisesRegex(ValueError,"SYMLINK"):
                ProspectiveSourceManifestService(self.root)


if __name__=="__main__":
    unittest.main()
