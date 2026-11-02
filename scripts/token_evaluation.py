"""Developer-only memory evaluation entrypoint; no native sessions by default."""
from token_evaluation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
