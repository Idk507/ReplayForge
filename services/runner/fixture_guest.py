"""Fixed synthetic checkout benchmark guest program; execute only remotely.

This module is serialized as source into a remote VM, never imported to run
generated code on the control plane. It accepts no repository URLs or host paths.
"""

GUEST_PROGRAM = r"""
import hashlib, json, os, pathlib, resource, subprocess, sys, tempfile

resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
resource.setrlimit(resource.RLIMIT_FSIZE, (8*1024*1024, 8*1024*1024))
data = json.loads(sys.argv[1])
root = pathlib.Path(tempfile.mkdtemp(prefix="rf-checkout-"))
(root/"app").mkdir()
(root/"tests").mkdir()
(root/"app/__init__.py").write_text("")
(root/"tests/__init__.py").write_text("")
(root/"app/checkout.py").write_text(data["source"], encoding="utf-8")
(root/"tests/test_generated.py").write_text(data["test"], encoding="utf-8")
env = {"PATH":"/usr/local/bin:/usr/bin:/bin", "HOME":str(root),
       "GIT_AUTHOR_NAME":"ReplayForge Fixture",
       "GIT_AUTHOR_EMAIL":"fixture@example.invalid",
       "GIT_COMMITTER_NAME":"ReplayForge Fixture",
       "GIT_COMMITTER_EMAIL":"fixture@example.invalid",
       "GIT_AUTHOR_DATE":"2026-10-18T00:00:00Z",
       "GIT_COMMITTER_DATE":"2026-10-18T00:00:00Z",
       "GIT_CONFIG_NOSYSTEM":"1", "GIT_CONFIG_GLOBAL":"/dev/null"}
def git(*args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null",
        "-c", "core.autocrlf=false", *args],
        cwd=root, env=env, capture_output=True, timeout=10)
for args in (("init","-q"), ("add","app","tests"),
             ("commit","-qm","ReplayForge synthetic checkout baseline")):
    result = git(*args)
    if result.returncode:
        print(json.dumps({"status":"SETUP_FAILED","error_class":"GIT_SETUP_FAILED"}))
        sys.exit(2)
source_commit = git("rev-parse", "HEAD").stdout.decode().strip()
source_tree = git("rev-parse", "HEAD^{tree}").stdout.decode().strip()
if data.get("patch"):
    (root/"candidate.diff").write_text(data["patch"],encoding="utf-8")
    for args in (("apply","--check","candidate.diff"),("apply","candidate.diff")):
        if git(*args).returncode:
            print(json.dumps({"status":"PATCH_REJECTED","source_commit":source_commit}))
            sys.exit(2)
    if git("add","app").returncode:
        sys.exit(2)
tree = git("write-tree").stdout.decode().strip()
test_hash = hashlib.sha256((root/"tests/test_generated.py").read_bytes()).hexdigest()
result = subprocess.run([sys.executable,"-m","unittest","tests.test_generated"],
    cwd=root,env=env,capture_output=True,timeout=20)
output = result.stdout + result.stderr
print(json.dumps({"status":"EXECUTED","source_commit":source_commit,"source_tree":source_tree,
    "tree":tree,"test_sha256":test_hash,"exit_code":result.returncode,
    "output_sha256":hashlib.sha256(output).hexdigest(),
    "behavioral_failure":b"AssertionError" in output
        and b"RF_DUPLICATE_TRANSACTION" in output,
    "setup_error":any(x in output for x in
        (b"ImportError",b"SyntaxError",b"ModuleNotFoundError")),
    "tests_ran":b"Ran 1 test" in output,
    "uid":os.getuid(),
    "interfaces":sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir()),
    "credential_env_present":any(k in os.environ for k in
        ('NEBIUS_API_KEY','TAVILY_API_KEY','GITHUB_TOKEN'))}))
"""
