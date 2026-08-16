"""Project and coding agent utilities for Nano.

These helpers are intentionally small but real: they inspect a project, run tests,
and summarize repository health via existing system tools.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git_status(path: str = ".") -> dict:
    try:
        result = subprocess.run(["git", "status", "--short"], cwd=path, capture_output=True, text=True, timeout=20)
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[:12000],
            "stderr": result.stderr[:12000],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def run_project_tests(path: str = ".") -> dict:
    project = Path(path)
    if not project.exists():
        return {"success": False, "error": "project_path_not_found"}
    try:
        result = subprocess.run(["python", "-m", "pytest", "-q"], cwd=str(project), capture_output=True, text=True, timeout=60)
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[:12000],
            "stderr": result.stderr[:12000],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def project_summary(path: str = ".") -> dict:
    project = Path(path)
    git = git_status(str(project))
    tests = run_project_tests(str(project))
    return {
        "success": True,
        "path": str(project),
        "git": git,
        "tests": tests,
    }
