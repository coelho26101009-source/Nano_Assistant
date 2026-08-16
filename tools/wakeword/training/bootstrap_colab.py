from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import platform
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = ROOT / "tools" / "wakeword" / "training" / "config" / "nano.yaml"

MINIMUM_DEPENDENCIES = [
    "torch",
    "torchinfo",
    "torchmetrics",
    "torchaudio",
    "speechbrain",
    "audiomentations",
    "torch_audiomentations",
    "mutagen",
    "acoustics",
    "yaml",
    "pronouncing",
    "datasets",
]

RESOURCE_LABELS = {
    "piper_sample_generator_path": "Piper",
    "rir_paths": "RIR",
    "background_paths": "Background audio",
    "false_positive_validation_data_path": "Validation features",
    "feature_data_files": "ACAV features",
}


def _status_label(value: bool) -> str:
    return "READY" if value else "MISSING"


def detect_python() -> dict[str, Any]:
    info = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "ok": sys.version_info[:2] >= (3, 11),
    }
    return info


def detect_gpu() -> dict[str, Any]:
    result = {"cuda_available": False, "gpu_name": "UNKNOWN", "cuda_version": "UNKNOWN", "torch_installed": False}
    spec = importlib.util.find_spec("torch")
    if spec is None:
        return result
    try:
        import torch  # type: ignore
        result["torch_installed"] = True
        result["cuda_available"] = bool(torch.cuda.is_available())
        if result["cuda_available"]:
            result["gpu_name"] = torch.cuda.get_device_name(0)
            result["cuda_version"] = torch.version.cuda or "UNKNOWN"
        else:
            result["gpu_name"] = "NVIDIA GPU not active in this environment"
    except Exception:
        return result
    return result


def detect_package(name: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(name)
    if spec is None:
        return {"name": name, "installed": False, "version": "MISSING"}
    try:
        version = importlib.metadata.version(name)
    except Exception:
        version = "UNKNOWN"
    return {"name": name, "installed": True, "version": version}


def parse_training_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("Training config must be a YAML mapping.")
    return data


def _resource_exists(path_val: Any) -> bool:
    if path_val is None or path_val == "REQUIRED BEFORE TRAINING":
        return False
    if isinstance(path_val, (list, tuple)):
        return bool(path_val) and all(_resource_exists(item) for item in path_val)
    if not isinstance(path_val, str):
        return False
    candidate = Path(path_val).expanduser()
    return candidate.exists()


def validate_resources(config: dict[str, Any]) -> dict[str, Any]:
    resources = {
        "piper_sample_generator_path": _resource_exists(config.get("piper_sample_generator_path")),
        "rir_paths": _resource_exists(config.get("rir_paths")),
        "background_paths": _resource_exists(config.get("background_paths")),
        "false_positive_validation_data_path": _resource_exists(config.get("false_positive_validation_data_path")),
        "feature_data_files": False,
    }
    features = config.get("feature_data_files") or {}
    if isinstance(features, dict):
        feature_paths = [str(v) for v in features.values() if isinstance(v, str)]
        resources["feature_data_files"] = bool(feature_paths) and all(_resource_exists(v) for v in feature_paths)
    return resources


def dependency_status() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for module in MINIMUM_DEPENDENCIES:
        result[module] = detect_package(module)
    return result


def print_resource_block(name: str, status: bool, *, detail: str = "") -> None:
    label = _status_label(status)
    print(f"{name:<28} {label}")
    if detail:
        print(f"  {detail}")


def print_download_plan(resource_name: str, *, source: str, size: str, destination: str, purpose: str) -> None:
    print(f"Resource: {resource_name}")
    print(f"Source: {source}")
    print(f"Approximate size: {size}")
    print(f"Destination: {destination}")
    print(f"Required for: {purpose}")


def _script_downloads() -> dict[str, dict[str, str]]:
    return {
        "piper": {
            "source": "Official Piper project or repository reference used by openWakeWord synthetic sample generation",
            "size": "UNKNOWN",
            "destination": "local piper project checkout / generator path",
            "purpose": "synthetic positive sample generation for Nano",
        },
        "rir": {
            "source": "Open RIR dataset or equivalent audio impulse responses",
            "size": "UNKNOWN",
            "destination": "rir_paths entry in the training config",
            "purpose": "augmentation and room-condition variation",
        },
        "background": {
            "source": "Open background audio dataset or curated library",
            "size": "UNKNOWN",
            "destination": "background_paths entries in the training config",
            "purpose": "noise and environmental negative samples",
        },
        "validation": {
            "source": "openWakeWord official false-positive validation feature set (~11h reference)",
            "size": "UNKNOWN",
            "destination": "false_positive_validation_data_path",
            "purpose": "false-positive validation for the wake-word model",
        },
        "features": {
            "source": "precomputed feature dataset (for example ACAV100M-derived feature archive)",
            "size": "UNKNOWN",
            "destination": "feature_data_files mapping",
            "purpose": "precomputed feature validation and training input",
        },
    }


def run_bootstrap(config_path: Path | str = DEFAULT_CONFIG_PATH, dry_run: bool = True, *, download_piper: bool = False, download_rirs: bool = False, download_background: bool = False, download_validation: bool = False, download_features: bool = False) -> int:
    python_info = detect_python()
    gpu_info = detect_gpu()
    config = parse_training_config(config_path)
    resources = validate_resources(config)
    deps = dependency_status()

    print("NANO WAKEWORD TRAINING BOOTSTRAP")
    print("=" * 60)
    print("Configuration:")
    print(f"  path: {Path(config_path)}")
    print(f"  python: {python_info['python_version']} | {'OK' if python_info['ok'] else 'NEEDS PY3.11+'}")
    print(f"  platform: {python_info['platform']}")
    print()
    print("PyTorch / CUDA:")
    print(f"  torch installed: {'YES' if gpu_info['torch_installed'] else 'NO'}")
    print(f"  CUDA available: {'YES' if gpu_info['cuda_available'] else 'NO'}")
    print(f"  GPU name: {gpu_info['gpu_name']}")
    print(f"  CUDA version: {gpu_info['cuda_version']}")
    print()

    print("Dependencies:")
    for dep_name in MINIMUM_DEPENDENCIES:
        info = deps.get(dep_name, {"installed": False, "version": "MISSING"})
        print(f"  {dep_name:<25} {'READY' if info['installed'] else 'MISSING'} {info['version']}")
    print()

    print("Resources:")
    print_resource_block("Piper", resources.get("piper_sample_generator_path", False), detail="path required for synthetic positive sample generation")
    print_resource_block("RIR", resources.get("rir_paths", False), detail="impulse response augmentation data")
    print_resource_block("Background audio", resources.get("background_paths", False), detail="noise/mixed negative audio")
    print_resource_block("Validation features", resources.get("false_positive_validation_data_path", False), detail="false-positive validation / quality gate")
    print_resource_block("ACAV features", resources.get("feature_data_files", False), detail="precomputed .npy feature data")
    print()

    if dry_run:
        print("Dry-run mode: no downloads or training will be started.")
    else:
        print("Download mode: reserved for explicit user approval only.")

    if any([download_piper, download_rirs, download_background, download_validation, download_features]):
        print("\nExplicit download requests received:")
        downloads = _script_downloads()
        if download_piper:
            print_download_plan("Piper", **downloads["piper"])
        if download_rirs:
            print_download_plan("RIR", **downloads["rir"])
        if download_background:
            print_download_plan("Background audio", **downloads["background"])
        if download_validation:
            print_download_plan("Validation features", **downloads["validation"])
        if download_features:
            print_download_plan("ACAV features", **downloads["features"])
        print("\nNo download has been executed by this bootstrap script. Review the request and run the actual fetch manually in Colab.")
        return 0

    print("\nStatus summary:")
    for key, label in RESOURCE_LABELS.items():
        value = resources.get(key, False)
        print(f"  {label:<28} {_status_label(value)}")
    print()
    print("Recommended next action: provide the external resources listed above or use the explicit download flags only when you decide to fetch them.")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap the openWakeWord 0.6.0 training environment without starting training.")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG_PATH), help="Training config file to validate.")
    parser.add_argument("--dry-run", action="store_true", help="Validate dependencies and resources without downloading anything.")
    parser.add_argument("--download-piper", action="store_true", help="Print the planned Piper download information without executing it.")
    parser.add_argument("--download-rirs", action="store_true", help="Print the planned RIR download information without executing it.")
    parser.add_argument("--download-background", action="store_true", help="Print the planned background audio download information without executing it.")
    parser.add_argument("--download-validation", action="store_true", help="Print the planned validation dataset information without executing it.")
    parser.add_argument("--download-features", action="store_true", help="Print the planned feature-data download information without executing it.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if not args.dry_run and not any([args.download_piper, args.download_rirs, args.download_background, args.download_validation, args.download_features]):
        args.dry_run = True
    return run_bootstrap(
        args.config,
        dry_run=args.dry_run,
        download_piper=args.download_piper,
        download_rirs=args.download_rirs,
        download_background=args.download_background,
        download_validation=args.download_validation,
        download_features=args.download_features,
    )


if __name__ == "__main__":
    raise SystemExit(main())
