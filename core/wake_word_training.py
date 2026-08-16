from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

OFFICIAL_CONFIG_KEYS: set[str] = {
    "model_name",
    "target_phrase",
    "custom_negative_phrases",
    "n_samples",
    "n_samples_val",
    "tts_batch_size",
    "augmentation_batch_size",
    "piper_sample_generator_path",
    "output_dir",
    "rir_paths",
    "background_paths",
    "background_paths_duplication_rate",
    "false_positive_validation_data_path",
    "augmentation_rounds",
    "feature_data_files",
    "batch_n_per_class",
    "model_type",
    "layer_size",
    "steps",
    "max_negative_weight",
    "target_false_positives_per_hour",
}

REQUIRED_RESOURCE_MARKER = "REQUIRED BEFORE TRAINING"

DEFAULT_TRAINING_CONFIG: dict[str, Any] = {
    "model_name": "nano",
    "target_phrase": ["Nano"],
    "custom_negative_phrases": [],
    "n_samples": 20000,
    "n_samples_val": 4000,
    "tts_batch_size": 32,
    "augmentation_batch_size": 64,
    "piper_sample_generator_path": REQUIRED_RESOURCE_MARKER,
    "output_dir": "models/wakeword/train-output",
    "rir_paths": [],
    "background_paths": [],
    "background_paths_duplication_rate": [],
    "false_positive_validation_data_path": REQUIRED_RESOURCE_MARKER,
    "augmentation_rounds": 2,
    "feature_data_files": {},
    "batch_n_per_class": 32,
    "model_type": "dnn",
    "layer_size": 32,
    "steps": 20000,
    "max_negative_weight": 1000,
    "target_false_positives_per_hour": 0.5,
}


def default_training_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "tools" / "wakeword" / "training" / "config" / "nano.yaml"


def load_training_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else default_training_config_path()
    if not config_path.exists():
        return DEFAULT_TRAINING_CONFIG.copy()
    with config_path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError("Wake-word training config must be a YAML mapping.")
    merged = DEFAULT_TRAINING_CONFIG.copy()
    merged.update(loaded)
    if "target_phrase" in loaded and isinstance(loaded["target_phrase"], str):
        merged["target_phrase"] = [loaded["target_phrase"]]
    return merged


def validate_training_config(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(config or {})
    issues: list[str] = []
    missing: list[str] = []
    unknown: list[str] = sorted(set(cfg) - OFFICIAL_CONFIG_KEYS)

    required_keys = list(OFFICIAL_CONFIG_KEYS)
    for key in required_keys:
        if key not in cfg:
            issues.append(f"missing required key: {key}")
            missing.append(key)

    if "model_name" in cfg and not isinstance(cfg["model_name"], str):
        issues.append("model_name must be a string")
    if "target_phrase" in cfg:
        phrase_list = cfg["target_phrase"]
        if isinstance(phrase_list, str):
            phrase_list = [phrase_list]
        if not isinstance(phrase_list, list) or not phrase_list or not all(isinstance(item, str) for item in phrase_list):
            issues.append("target_phrase must be a non-empty list of strings")
    for numeric_key in ["n_samples", "n_samples_val", "tts_batch_size", "augmentation_batch_size", "augmentation_rounds", "batch_n_per_class", "layer_size", "steps", "max_negative_weight"]:
        if numeric_key in cfg and not isinstance(cfg[numeric_key], (int, float)):
            issues.append(f"{numeric_key} must be numeric")
        elif numeric_key in cfg and float(cfg[numeric_key]) <= 0:
            issues.append(f"{numeric_key} must be > 0")
    if "target_false_positives_per_hour" in cfg and (not isinstance(cfg["target_false_positives_per_hour"], (int, float)) or float(cfg["target_false_positives_per_hour"]) <= 0):
        issues.append("target_false_positives_per_hour must be > 0")
    if "model_type" in cfg and cfg["model_type"] not in {"dnn", "rnn"}:
        issues.append("model_type must be 'dnn' or 'rnn'")
    if "output_dir" in cfg and not isinstance(cfg["output_dir"], str):
        issues.append("output_dir must be a string path")
    if "piper_sample_generator_path" in cfg and cfg["piper_sample_generator_path"] == REQUIRED_RESOURCE_MARKER:
        missing.append("piper_sample_generator_path")
    if "false_positive_validation_data_path" in cfg and cfg["false_positive_validation_data_path"] == REQUIRED_RESOURCE_MARKER:
        missing.append("false_positive_validation_data_path")
    if "feature_data_files" in cfg and not isinstance(cfg["feature_data_files"], dict):
        issues.append("feature_data_files must be a mapping")
    if "rir_paths" in cfg and not isinstance(cfg["rir_paths"], list):
        issues.append("rir_paths must be a list")
    if "background_paths" in cfg and not isinstance(cfg["background_paths"], list):
        issues.append("background_paths must be a list")
    if "background_paths_duplication_rate" in cfg and not isinstance(cfg["background_paths_duplication_rate"], list):
        issues.append("background_paths_duplication_rate must be a list")

    result = {
        "ok": not issues and not missing,
        "schema_valid": not issues,
        "unknown_keys": unknown,
        "missing_keys": missing,
        "issues": issues,
        "required_before_training": sorted(set(missing) | {"piper_sample_generator_path" if cfg.get("piper_sample_generator_path") == REQUIRED_RESOURCE_MARKER else None, "false_positive_validation_data_path" if cfg.get("false_positive_validation_data_path") == REQUIRED_RESOURCE_MARKER else None}),
    }
    result["required_before_training"] = [item for item in result["required_before_training"] if item]
    return result


def build_model_metadata(model_name: str, target_phrase: str | list[str], *, provider: str = "openwakeword", model_format: str = "onnx", threshold: float = 0.7, training_version: str = "v1") -> dict[str, Any]:
    phrase_list = [target_phrase] if isinstance(target_phrase, str) else list(target_phrase)
    return {
        "model_name": model_name,
        "target_phrase": phrase_list,
        "training_version": training_version,
        "provider": provider,
        "model_format": model_format,
        "threshold": threshold,
        "created_at": "TBD",
        "notes": "Custom wake-word model for Nano; placeholder metadata until actual training/export is completed.",
    }


def write_model_metadata(metadata_path: str | Path, metadata: dict[str, Any]) -> Path:
    target = Path(metadata_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def resolve_versioned_model_path(base_dir: str | Path, version: str = "v1") -> Path:
    root = Path(base_dir)
    return root / f"nano-{version}.onnx"


def validate_model_metadata(metadata: dict[str, Any]) -> bool:
    required = ["model_name", "target_phrase", "training_version", "provider", "model_format"]
    return all(isinstance(metadata.get(key), (str, list)) for key in required)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the Nano wake-word training YAML against the installed openWakeWord 0.6.0 schema without running training.")
    parser.add_argument("--config", type=str, default=str(default_training_config_path()), help="Path to the training YAML file to validate")
    parser.add_argument("--dry-run", action="store_true", help="Perform a schema validation dry-run only")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = load_training_config(args.config)
    result = validate_training_config(config)
    print("Configuration:")
    print("VALID" if result["schema_valid"] else "INVALID")
    print()
    print("Training:")
    print("NOT STARTED")
    print()
    if result["unknown_keys"]:
        print("Unknown keys:")
        for key in result["unknown_keys"]:
            print(f"- {key}")
        print()
    if result["missing_keys"]:
        print("Missing external resources:")
        for key in result["missing_keys"]:
            print(f"- {key}: REQUIRED BEFORE TRAINING")
        print()
    if result["issues"]:
        print("Issues:")
        for issue in result["issues"]:
            print(f"- {issue}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
