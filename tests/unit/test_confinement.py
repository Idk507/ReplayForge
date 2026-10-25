"""Guest confinement planning only: no root operations on the developer host."""

import ast
from uuid import uuid4

import pytest

from packages.contracts.sandbox import RunnerError
from services.runner.confinement import (
    CONFINEMENT_PROBE,
    confined_test_request,
    confinement_probe_request,
    seccomp_program,
)


def test_privileged_setup_is_explicit_and_never_selects_untrusted_commands():
    image = uuid4()
    with pytest.raises(RunnerError, match="CONFINEMENT_SETUP_NOT_AUTHORIZED"):
        confinement_probe_request(image)
    payload = confinement_probe_request(image, allow_privileged_setup=True)
    assert payload["image"] == str(image)
    assert payload["uid"] == payload["gid"] == 0
    assert payload["networking"] == {"enabled": False}
    assert payload["env"] == {} and payload["preserve_env"] is False
    assert payload["shell"] is False and payload["disposable"] is True
    assert payload["args"] == ["-I", "-S", "-c", CONFINEMENT_PROBE]
    ast.parse(CONFINEMENT_PROBE)  # Parse trusted code; never execute it locally.


def test_confinement_probe_rejects_untyped_image_identity():
    with pytest.raises(RunnerError):
        confinement_probe_request("latest", allow_privileged_setup=True)


def test_confined_workload_is_only_the_exact_measured_plan():
    from services.runner.commands import plan_pytest
    from tests.unit.test_command_policy import inputs

    run, grant, owner, request = inputs()
    plain = plan_pytest(request, run, grant, owner)
    with pytest.raises(RunnerError):
        confined_test_request(plain, uuid4(), uuid4(), allow_privileged_setup=True)
    plan = plan_pytest(request, run, grant, owner, measured_workspace=True)
    payload = confined_test_request(plan, uuid4(), uuid4(), allow_privileged_setup=True)
    assert payload["disposable"] is False and payload["timeout"] == 60
    assert payload["uid"] == 0 and payload["networking"] == {"enabled": False}
    ast.parse(payload["args"][3])
    assert "'argv': " + repr(list(plan.argv)) in payload["args"][3]


def filter_result(number, *arguments, arch=0xC000003E):
    """Interpret only the emitted classic-BPF vocabulary; no kernel execution."""
    words = {0: number, 4: arch}
    for index, argument in enumerate((*arguments, 0, 0, 0, 0, 0, 0)[:6]):
        words[16 + index * 8] = argument & 0xFFFFFFFF
        words[20 + index * 8] = argument >> 32
    accumulator, pc = 0, 0
    program = seccomp_program()
    for _ in range(len(program) + 1):
        code, yes, no, value = program[pc]
        pc += 1
        if code == 0x20:
            accumulator = words[value]
        elif code == 0x54:
            accumulator &= value
        elif code == 0x15:
            pc += yes if accumulator == value else no
        elif code == 0x35:
            pc += yes if accumulator >= value else no
        elif code == 0x06:
            return value
        else:
            pytest.fail("unexpected BPF instruction")
    pytest.fail("unterminated BPF program")


@pytest.mark.parametrize(
    "number", [41, 42, 46, 57, 58, 101, 165, 272, 298, 310, 311, 321, 425, 438]
)
def test_filter_denies_process_network_namespace_and_inspection_escape(number):
    assert filter_result(number) == 0x00050001


def test_filter_rejects_other_abis_and_clone3_has_explicit_safe_fallback():
    assert filter_result(0, arch=0x40000003) == 0x80000000
    assert filter_result(0x40000000) == 0x80000000
    assert filter_result(435) == 0x00050026


def test_threads_cannot_add_namespaces_or_fork_a_separate_address_space():
    assert filter_result(56, 0x10100) == 0x7FFF0000
    for flag in (0, 0x100, 0x10000, 0x10100 | 0x20000000, 0x10100 | 0x02000000):
        assert filter_result(56, flag) == 0x00050001


def test_anonymous_socketpair_cannot_send_to_a_named_unix_or_network_endpoint():
    assert filter_result(53, 1) == 0x7FFF0000
    assert filter_result(53, 2) == 0x00050001
    assert filter_result(53, 40) == 0x00050001
    assert filter_result(44, 3, 123, 8, 0, 0, 0) == 0x7FFF0000
    for address, length in ((1, 0), (0, 1), (2**32, 0), (0, 2**32)):
        assert filter_result(44, 3, 123, 8, 0, address, length) == 0x00050001
    assert filter_result(1) == 0x7FFF0000  # ordinary stdout write
