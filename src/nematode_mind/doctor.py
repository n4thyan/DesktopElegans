from __future__ import annotations
import importlib
import shutil
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_checks() -> list[Check]:
    checks: list[Check] = []
    py_ok = sys.version_info[:2] == (3, 10)
    checks.append(Check("python", py_ok, f"{sys.version.split()[0]} (c302 Windows setup expects 3.10)"))

    git = shutil.which("git")
    checks.append(Check("git", bool(git), git or "git executable not found"))

    repo = Path("vendor/c302")
    if repo.exists() and git:
        try:
            proc = subprocess.run([git, "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
            head = proc.stdout.strip() if proc.returncode == 0 else (proc.stderr.strip() or "unknown")
            expected = "6cd861f8ca4d3241ee9cf4627884caa930dab53c"
            checks.append(Check("c302 revision", proc.returncode == 0 and head == expected, f"{head} expected={expected}"))
        except Exception as exc:
            checks.append(Check("c302 revision", False, str(exc)))
    else:
        checks.append(Check("c302 revision", False, "vendor/c302 checkout not found"))

    java = shutil.which("java")
    if java:
        try:
            proc = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=10)
            detail = (proc.stderr or proc.stdout).splitlines()[0] if (proc.stderr or proc.stdout) else java
            checks.append(Check("java", proc.returncode == 0, detail))
        except Exception as exc:
            checks.append(Check("java", False, str(exc)))
    else:
        checks.append(Check("java", False, "java executable not found"))

    for module in ("c302", "pyneuroml", "neuroml", "yaml"):
        try:
            m = importlib.import_module(module)
            version = getattr(m, "__version__", "installed")
            checks.append(Check(module, True, str(version)))
        except Exception as exc:
            checks.append(Check(module, False, f"{type(exc).__name__}: {exc}"))

    try:
        from pyneuroml import pynml
        detail = "run_lems_with_jneuroml available" if hasattr(pynml, "run_lems_with_jneuroml") else "missing run_lems_with_jneuroml"
        checks.append(Check("jNeuroML API", hasattr(pynml, "run_lems_with_jneuroml"), detail))
    except Exception as exc:
        checks.append(Check("jNeuroML API", False, f"{type(exc).__name__}: {exc}"))

    return checks


def print_checks() -> bool:
    checks = run_checks()
    width = max(len(c.name) for c in checks)
    for c in checks:
        mark = "OK" if c.ok else "FAIL"
        print(f"[{mark:4}] {c.name:<{width}}  {c.detail}")
    return all(c.ok for c in checks)
