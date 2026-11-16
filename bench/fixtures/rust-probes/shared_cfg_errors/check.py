"""Compile every case here with and without `--cfg test`, and print what rustc says.

    python bench/fixtures/rust-probes/shared_cfg_errors/check.py [--toolchain DIR]

Each case begins with `// expect: <error code>`: with `--cfg test` rustc must reject it with that code, and without it the program must
compile (the second definition is legal only in the build where the call that could reach it does not exist). Run by hand, like
bench/rs_probe_oracle.py; needs rustc (default /tmp/rust-toolchain/cargo/bin, or $RIPPLE_RUST_TOOLCHAIN). Exit status 1 if a case differs.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def rustc_of(argv) -> str:
    toolchain = Path(os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    if "--toolchain" in argv:
        toolchain = Path(argv[argv.index("--toolchain") + 1])
    return str(toolchain / "cargo" / "bin" / "rustc")


def compile_case(rustc: str, case: Path, test: bool) -> tuple[bool, list[str]]:
    with tempfile.TemporaryDirectory() as out:
        cmd = [rustc, "--edition", "2021", "--crate-type", "lib", "--emit", "metadata", "--out-dir", out, str(case)]
        if test:
            cmd[1:1] = ["--cfg", "test"]
        done = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", "/tmp/rust-toolchain/rustup")})
    return done.returncode == 0, re.findall(r"^error\[(E\d+)\]", done.stderr, re.M)


def main(argv) -> int:
    rustc = rustc_of(argv)
    bad = 0
    for case in sorted(HERE.glob("*.rs")):
        expect = re.match(r"// expect: (\w+)", case.read_text()).group(1)
        with_test, codes = compile_case(rustc, case, True)
        without, _ = compile_case(rustc, case, False)
        ok = not with_test and expect in codes and without
        bad += not ok
        print(f"{case.name}: with --cfg test: {'compiles' if with_test else 'rejected ' + ','.join(codes)}; without: {'compiles' if without else 'rejected'} {'ok' if ok else 'UNEXPECTED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
