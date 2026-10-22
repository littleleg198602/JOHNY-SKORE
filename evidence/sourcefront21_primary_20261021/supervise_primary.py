from pathlib import Path
import ast, hashlib, json, os, subprocess
from datetime import datetime,timezone
root=Path("/workspace/scratch/334b3a149083/JOHNY-batch7-integration-20261015")
out=Path("/workspace/scratch/334b3a149083/sourcefront21_20261021")
planpath=root/"evidence/sourcefront21_primary_20261021/request_plan.json"
helper=root/"evidence/sourcefront21_primary_20261021/capture_primary.py"
p=planpath.read_bytes()
assert hashlib.sha256(p).hexdigest()=="f73ed0625350ef8a1c51eb20cbec9a484f483c7f9071f3bbe3bf1e7eed06dbf0"
ast.parse(helper.read_text())
assert not (helper.parent/"primary").exists(), "Prior reservation/capture may exist; do not repeat"
for key in ("source_plan","original_literal_parent"):
 for row in json.loads(p)["urls"]:
  pin=row[key]; raw=(root/pin["path"]).read_bytes()
  assert len(raw)==pin["bytes"] and hashlib.sha256(raw).hexdigest()==pin["sha256"]
audit=json.loads(p)["independent_candidate_audit"]
raw=(root/audit["path"]).read_bytes()
assert len(raw)==audit["bytes"] and hashlib.sha256(raw).hexdigest()==audit["sha256"]
now=lambda:datetime.now(timezone.utc).isoformat()
def save(name,obj):
 with (out/name).open("x",encoding="utf-8") as f:
  f.write(json.dumps(obj,indent=2)+"\n"); f.flush(); os.fsync(f.fileno())
cmd=["/workspace/scratch/334b3a149083/johny-tests-20261011/bin/python","-u","-c","import runpy;runpy.run_path('evidence/sourcefront21_primary_20261021/capture_primary.py',run_name='__main__')"]
save("primary_capture_launch.json",dict(started_at=now(),command=cmd,plan_sha256=hashlib.sha256(p).hexdigest(),helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),http_attempts_before=0,maximum_transports=5,retry=False,redirect=False,fallback=False))
with (out/"primary_capture_stdout.log").open("xb") as stdout, (out/"primary_capture_stderr.log").open("xb") as stderr:
 proc=subprocess.Popen(cmd,cwd=root,stdout=stdout,stderr=stderr,start_new_session=True)
 save("primary_capture_process.json",dict(pid=proc.pid,observed_at=now()))
 code=proc.wait()
save("primary_capture_exit.json",dict(completed_at=now(),exit_code=code))
print(json.dumps(dict(exit_code=code,completed_at=now())))
