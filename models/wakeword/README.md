# Nano wake-word models

This directory is reserved for the final exported runtime models used by the Nano system.

Expected layout:

```text
models/
  wakeword/
    nano.onnx
    nano-v1.onnx
    metadata.json
    README.md
```

Do not train or export directly inside the main runtime environment unless the model has been validated elsewhere.
