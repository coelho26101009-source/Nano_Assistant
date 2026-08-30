# Nano embedded runtime

Esta pasta é preenchida pelo pipeline de release. O instalador inclui o Python runtime e as dependências necessárias para executar o Nano sem Python/Node instalados no PC.

Estrutura esperada no build:

```text
runtime/
  python/
    python.exe
    Lib/
    DLLs/
  wheels/          # opcional: cache offline durante a preparação do runtime
```

O runtime não deve conter `.env`, tokens, bases de dados pessoais ou segredos.
