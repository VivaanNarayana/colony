# Install Colony in a dedicated environment

Use Python 3.11–3.13 with SQLite extension support. Keep the application environment,
private team workspace, model directory, and evidence repository separate. The wheel
contains the CLI; source checkout tools and development extras are unnecessary at runtime.

Create an environment and use its Python executable directly if activation is unavailable:

```powershell
# Windows PowerShell, from the selected Colony source checkout
python -m venv .venv
.venv\Scripts\python.exe -m pip wheel --no-deps --wheel-dir dist .
.venv\Scripts\python.exe -m pip install dist\colony_memory-0.1.0-py3-none-any.whl
.venv\Scripts\python.exe -E -P -m colony.cli doctor --role server
```

```sh
# Linux/macOS, from the selected Colony source checkout
python3 -m venv .venv
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
.venv/bin/python -m pip install dist/colony_memory-0.1.0-py3-none-any.whl
.venv/bin/python -E -P -m colony.cli doctor --role server
```

The final command should identify missing models before preparation. Run commands using
the dedicated environment's `colony` executable, activate that environment, or continue
using its Python with `-E -P -m colony.cli`. Generated onboarding commands already name
that interpreter and quote paths for the host shell (PowerShell on Windows).

If using a built wheel rather than a checkout, create a fresh environment and install
the exact wheel path with its Python. Confirm the artifact's SHA-256 against the retained
build manifest. Do not assume an unrelated package-registry artifact matches this checkout.
See the [guided onboarding](README.md#guided-installation-and-onboarding) for the next steps.

## Build and verify distributable artifacts

For a local release candidate, install the standard build frontend in a build environment:

```sh
python -m pip install build
python -m build
```

This produces a wheel and source distribution under `dist/`. Record their hashes with
PowerShell `Get-FileHash -Algorithm SHA256 dist/*` or `sha256sum dist/*` on Unix. Rebuild a
wheel from the source distribution and test installation in a new environment outside the
source checkout. Run `colony --help`, a read-only doctor check, explicit model preparation,
and the isolated demo there. These are build tools, not additional runtime dependencies.

Use only model preparation's explicit network action while provisioning. Then verify the
offline demo on the actual target host; a local file check alone cannot establish ONNX
runtime compatibility. Preserve diagnostic failures and report platform/symlink coverage
gaps. This change does not publish to a registry or create a hosted deployment.
