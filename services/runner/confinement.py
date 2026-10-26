"""Remote-only fixed confinement templates; no implicit execution authority.

The fixed supervisor uses guest root for setup, then runs only a fixed diagnostic
as uid/gid1000. Workloads use an admitted measured pytest plan exclusively. No
capability signature, execution authority or verification verdict is produced.
"""

import json
from hashlib import sha256
from uuid import UUID

from packages.contracts.execution import TestCommandPlan
from packages.contracts.sandbox import RunnerError
from services.runner.receipts import plan_ref


def seccomp_program() -> tuple[tuple[int, int, int, int], ...]:
    """Build the fixed x86_64 policy without executing guest code or syscalls."""
    allow, deny, kill = 0x7FFF0000, 0x00050001, 0x80000000
    # Exact x86_64 ABI; reject x32 syscall numbers before any syscall rules.
    code: list[tuple[int, int, int, int]] = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, 0xC000003E),
        (0x06, 0, 0, kill),
        (0x20, 0, 0, 0),
        (0x35, 0, 1, 0x40000000),
        (0x06, 0, 0, kill),
    ]
    # Only same-address-space threads may clone. No new namespaces/processes.
    thread = [
        (0x20, 0, 0, 16),
        (0x54, 0, 0, 0x7E020000),
        (0x15, 1, 0, 0),
        (0x06, 0, 0, deny),
        (0x20, 0, 0, 16),
        (0x54, 0, 0, 0x10100),
        (0x15, 0, 1, 0x10100),
        (0x06, 0, 0, allow),
        (0x06, 0, 0, deny),
    ]
    code += [(0x15, 0, len(thread), 56)] + thread
    for number in (53,):
        # Only anonymous UNIX socketpairs for event loops. socket() is denied:
        # an unconnected UNIX socket could otherwise sendto a named service.
        rule = [
            (0x20, 0, 0, 16),
            (0x15, 0, 1, 1),
            (0x06, 0, 0, allow),
            (0x06, 0, 0, deny),
        ]
        code += [(0x15, 0, len(rule), number)] + rule
    # sendto may only address the already-connected anonymous socketpair. Even
    # UNIX datagram pairs must not send to a named control/metadata socket.
    rule = []
    for offset in (48, 52, 56, 60):
        rule += [(0x20, 0, 0, offset), (0x15, 1, 0, 0), (0x06, 0, 0, deny)]
    rule += [(0x06, 0, 0, allow)]
    code += [(0x15, 0, len(rule), 44)] + rule
    # clone3 must return ENOSYS so glibc can fall back to filtered clone.
    code += [(0x15, 0, 1, 435), (0x06, 0, 0, 0x00050026)]
    for number in (
        41,
        42,
        43,
        46,
        49,
        50,
        57,
        58,
        101,
        155,
        161,
        165,
        166,
        169,
        172,
        173,
        175,
        176,
        288,
        307,
        246,
        248,
        249,
        250,
        272,
        298,
        303,
        304,
        308,
        310,
        311,
        313,
        321,
        323,
        425,
        426,
        427,
        428,
        429,
        430,
        431,
        432,
        438,
        442,
    ):
        code += [(0x15, 0, 1, number), (0x06, 0, 0, deny)]
    code += [(0x06, 0, 0, allow)]
    return tuple(code)


_FILTER_ASSIGNMENT = "FILTER_CODE = " + repr(seccomp_program()) + "\n"


_CHILD = r"""import errno, json, os, pathlib
status = {}
for line in pathlib.Path('/proc/self/status').read_text().splitlines():
    if line.split(':', 1)[0] in ('CapEff', 'NoNewPrivs', 'Seccomp'):
        k, v = line.split(':', 1); status[k] = v.strip()
denied = False
try:
    with open('/etc/replayforge-root-write-probe', 'x') as output:
        output.write('unexpected')
except OSError as error:
    denied = error.errno in (errno.EROFS, errno.EACCES)
fork_denied = False
try:
    pid = os.fork()
    if pid == 0: os._exit(99)
    os.waitpid(pid, 0)
except OSError as error:
    fork_denied = error.errno == errno.EPERM
tmp = pathlib.Path('/tmp/replayforge-write-probe')
tmp.write_text('bounded')
tmp.unlink()
limits = {name: (pathlib.Path('__CGROUP__') / name).read_text().strip()
          for name in ('cpu.max', 'memory.max', 'pids.max')}
print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'process': status,
    'root_mount_read_only': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
    'root_write_denied': denied, 'fork_denied': fork_denied,
    'tmp_bytes': os.statvfs('/tmp').f_blocks * os.statvfs('/tmp').f_frsize,
    'tmp_inodes': os.statvfs('/tmp').f_files, 'cgroup': limits}))
"""

_SETUP = r"""import ctypes, json, os, pathlib, platform, subprocess, sys, uuid
assert os.getuid() == 0 and platform.machine() == 'x86_64'
libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                      ctypes.c_ulong, ctypes.c_char_p]
libc.prctl.argtypes = [ctypes.c_ulong] * 5
def checked(value):
    if value != 0: raise OSError(ctypes.get_errno(), 'CONFINEMENT_SETUP_FAILED')
def mount(source, target, kind, flags, data=None):
    checked(libc.mount(source, target, kind, flags, data))
cg = pathlib.Path('/sys/fs/cgroup')
cg.mkdir(parents=True, exist_ok=True)
if not (cg / 'cgroup.controllers').exists():
    mount(b'none', bytes(cg), b'cgroup2', 0)
assert all(v in (cg / 'cgroup.controllers').read_text().split()
           for v in ('cpu', 'memory', 'pids'))
(cg / 'cgroup.subtree_control').write_text('+cpu +memory +pids')
group = cg / ('replayforge-' + uuid.uuid4().hex)
group.mkdir()
for name, value in (('cpu.max', '100000 100000'),
                    ('memory.max', '134217728'), ('memory.swap.max', '0'),
                    ('pids.max', '16')):
    (group / name).write_text(value)

class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte),
                ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]
class Program(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ushort), ('filter', ctypes.POINTER(Filter))]
def install_seccomp():
    code = FILTER_CODE
    filters = (Filter * len(code))(*(Filter(*entry) for entry in code))
    program = Program(len(code), filters)
    checked(libc.prctl(38, 1, 0, 0, 0))
    address = ctypes.cast(ctypes.pointer(program), ctypes.c_void_p).value
    checked(libc.prctl(22, 2, address, 0, 0))

def confine():
    os.setsid()
    (group / 'cgroup.procs').write_text('0')
    checked(libc.unshare(0x00020000))
    mount(None, b'/', None, 0x40000 | 0x4000)  # MS_PRIVATE | MS_REC
    mount(b'/', b'/', None, 0x1000 | 0x4000)  # MS_BIND | MS_REC
    # Freeze every inherited filesystem mount, not merely the root mount.
    lines = pathlib.Path('/proc/self/mountinfo').read_text().splitlines()
    points = {line.split()[4] for line in lines}
    for point in sorted(points, key=lambda p: (-p.count('/'), p)):
        if globals().get('WORKLOAD') and point in ('/tmp', '/workspace/repo'):
            continue
        assert '\\' not in point
        existing = os.statvfs(point).f_flag & (2 | 4 | 8)
        mount(None, point.encode(), None, 0x1000 | 0x20 | 1 | existing)
    if not globals().get('WORKLOAD'):
        mount(b'tmpfs', b'/tmp', b'tmpfs', 2 | 4 | 8,
              b'size=16777216,nr_inodes=256,mode=1777')
    os.chdir('/workspace/repo' if globals().get('WORKLOAD') else '/tmp')
    os.setgroups([])
    os.setresgid(1000, 1000, 1000)
    os.setresuid(1000, 1000, 1000)
    install_seccomp()
    if globals().get('WORKLOAD'):
        status = {}
        for line in pathlib.Path('/proc/self/status').read_text().splitlines():
            if line.split(':', 1)[0] in ('CapEff', 'NoNewPrivs', 'Seccomp'):
                k, v = line.split(':', 1); status[k] = v.strip()
        observation = {'uid': os.getuid(), 'gid': os.getgid(), 'process': status,
            'root_mount_read_only': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
            'tmp_bytes': os.statvfs('/tmp').f_blocks * os.statvfs('/tmp').f_frsize,
            'workspace_bytes': os.statvfs('/workspace/repo').f_blocks *
                               os.statvfs('/workspace/repo').f_frsize,
            'cgroup': {name: (group / name).read_text().strip()
                       for name in ('cpu.max', 'memory.max',
                                    'memory.swap.max', 'pids.max')}}
        assert observation['uid'] == observation['gid'] == 1000
        assert status == {'CapEff': '0000000000000000',
                          'NoNewPrivs': '1', 'Seccomp': '2'}
        assert observation['root_mount_read_only']
        os.write(STATUS_FD, json.dumps(observation).encode())
        os.close(STATUS_FD)
"""

_WORKLOAD_RUN = r"""
import re, resource, selectors, signal, stat, time
WORKLOAD = True
root = pathlib.Path('/workspace/repo')
assert root.resolve() == root and root.is_dir() and not root.is_symlink()
def read_tree():
    entries, total = {}, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = pathlib.Path(directory) / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            assert len(entries) < 256 and len(relative) <= 240
            assert re.fullmatch(r'[A-Za-z0-9_./-]+', relative)
            assert all(p not in ('', '.', '..') for p in relative.split('/'))
            assert stat.S_IMODE(mode) & ~0o777 == 0
            if stat.S_ISDIR(mode): entries[relative] = (stat.S_IMODE(mode), None)
            else:
                assert stat.S_ISREG(mode)
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as source: data = source.read(65537)
                total += len(data)
                assert total <= 65536
                entries[relative] = (stat.S_IMODE(mode), data)
    return entries
def write_tree(entries):
    for name, (mode, data) in sorted(entries.items()):
        path = root / name
        if data is None: path.mkdir(mode=mode)
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            fd = os.open(path, flags, mode)
            with os.fdopen(fd, 'wb') as destination: destination.write(data)
        path.chmod(mode)
        os.chown(path, 1000, 1000)
original = read_tree()
mount(b'tmpfs', b'/workspace/repo', b'tmpfs', 2 | 4 | 8,
      b'size=16777216,nr_inodes=256,mode=0755,uid=1000,gid=1000')
write_tree(original)
mount(b'tmpfs', b'/tmp', b'tmpfs', 2 | 4 | 8,
      b'size=16777216,nr_inodes=256,mode=1777')
os.chdir('/')
status_path = pathlib.Path('/tmp/replayforge-confinement.json')
STATUS_FD = os.open(status_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
stdout, stderr, count = bytearray(), bytearray(), 0
child = None
timed_out = output_limited = False
try:
    def prepare():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        confine()
    child = subprocess.Popen(CFG['argv'], cwd=str(root), env=CFG['env'],
        preexec_fn=prepare, pass_fds=(STATUS_FD,), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.close(STATUS_FD)
    selector = selectors.DefaultSelector()
    for stream, destination in ((child.stdout, stdout), (child.stderr, stderr)):
        selector.register(stream, selectors.EVENT_READ, destination)
    deadline = time.monotonic() + 55
    def stop():
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            # Already-terminal child has no process group to cancel.
            if child.poll() is None: raise
    while selector.get_map():
        if time.monotonic() >= deadline and child.poll() is None:
            timed_out = True; stop()
        for key, _ in selector.select(0.1):
            chunk = os.read(key.fd, 8192)
            if not chunk:
                selector.unregister(key.fileobj); continue
            count += len(chunk)
            if count > 65536:
                output_limited = True
                if child.poll() is None: stop()
            remaining = max(0, 65536 - len(stdout) - len(stderr))
            key.data.extend(chunk[:remaining])
        if timed_out or output_limited:
            if child.poll() is not None: break
    selector.close()
    code = child.wait(timeout=2)
    confinement = json.loads(status_path.read_text())
    report = pathlib.Path('/tmp/replayforge-result.xml')
    junit = None
    if report.exists() or report.is_symlink():
        fd = os.open(report, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        assert stat.S_ISREG(os.fstat(fd).st_mode)
        with os.fdopen(fd, 'rb') as source: junit = source.read(65537)
        assert len(junit) <= 65536
    final = read_tree()
finally:
    if child is not None and child.poll() is None:
        stop(); child.wait(timeout=2)
    group.rmdir()
    checked(libc.umount2(b'/workspace/repo', 0))
    checked(libc.umount2(b'/tmp', 0))
for name, (_, data) in sorted(original.items(), reverse=True):
    path = root / name
    path.rmdir() if data is None else path.unlink()
write_tree(final)
if junit is not None: pathlib.Path('/tmp/replayforge-result.xml').write_bytes(junit)
record = dict(CFG, confinement=confinement, exit_code=code, timed_out=timed_out,
              output_limited=output_limited)
pathlib.Path('/tmp/replayforge-execution.json').write_text(json.dumps(record))
pathlib.Path('/tmp/replayforge-execution.json').chmod(0o600)
sys.stdout.buffer.write(stdout); sys.stderr.buffer.write(stderr)
sys.exit(124 if timed_out or output_limited else (code if code >= 0 else 128 - code))
"""

_RUN = r"""
try:
    child = subprocess.run([sys.executable, '-I', '-S', '-c', CHILD],
        preexec_fn=confine, capture_output=True, timeout=5, env={}, cwd='/tmp')
    assert child.returncode == 0, 'CONFINED_DIAGNOSTIC_FAILED'
    report = json.loads(child.stdout)
    print(json.dumps(report))
finally:
    group.rmdir()
"""

CONFINEMENT_PROBE = (
    _FILTER_ASSIGNMENT
    + _SETUP
    + "\nCHILD = "
    + repr(_CHILD)
    + ".replace('__CGROUP__', str(group))\n"
    + _RUN
)


def confinement_probe_request(
    image_uuid: UUID, *, allow_privileged_setup: bool = False
) -> dict[str, object]:
    """Plan fixed guest setup only; no host operations or provider I/O occur."""
    if allow_privileged_setup is not True:
        raise RunnerError("CONFINEMENT_SETUP_NOT_AUTHORIZED")
    if not isinstance(image_uuid, UUID):
        raise RunnerError("CONFINEMENT_IMAGE_IDENTITY_DENIED")
    return {
        "image": str(image_uuid),
        "command": "/usr/local/bin/python",
        "args": ["-I", "-S", "-c", CONFINEMENT_PROBE],
        "uid": 0,
        "gid": 0,
        "shell": False,
        "env": {},
        "preserve_env": False,
        "networking": {"enabled": False},
        "cwd": "/tmp",
        "disposable": True,
        "timeout": 15,
        "truncate_output_at": 8192,
        "resources_limits": {"max_layer_bytes": 1024 * 1024},
    }


def confined_test_request(
    plan: TestCommandPlan,
    image_uuid: UUID,
    execution_id: UUID,
    *,
    allow_privileged_setup: bool = False,
) -> dict[str, object]:
    """Plan a fixed root supervisor for an already admitted measured pytest plan.

    The untrusted child is always uid/gid1000. This function grants no dispatch
    authority; capability assessment and durable claim remain mandatory.
    """
    plan = TestCommandPlan.model_validate(plan.model_dump())
    if plan.command_id != "PYTEST_FILES_MEASURED_V1" or not isinstance(
        execution_id, UUID
    ):
        raise RunnerError("CONFINEMENT_MEASURED_PLAN_REQUIRED")
    payload = confinement_probe_request(
        image_uuid, allow_privileged_setup=allow_privileged_setup
    )
    cfg = {
        "run_id": str(plan.request.run_id),
        "execution_id": str(execution_id),
        "plan_ref": plan_ref(plan),
        "argv": list(plan.argv),
        "env": dict(plan.env),
    }
    program = "CFG = " + repr(cfg) + "\n" + _FILTER_ASSIGNMENT + _SETUP + _WORKLOAD_RUN
    payload.update(
        args=["-I", "-S", "-c", program],
        cwd="/workspace/repo",
        disposable=False,
        timeout=plan.timeout_seconds,
        truncate_output_at=65536,
        resources_limits={"max_layer_bytes": 64 * 1024 * 1024},
    )
    return payload


def confinement_configuration_ref(
    plan: TestCommandPlan, image_uuid: UUID, execution_id: UUID
) -> str:
    """Bind admission to the complete fixed spawn payload, not a mutable label.

    Hashing a template is not an assessment of its controls or OCI provenance.
    The independent assessor must establish those before issuing admission.
    """
    payload = confined_test_request(
        plan, image_uuid, execution_id, allow_privileged_setup=True
    )
    wire = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + sha256(wire).hexdigest()
