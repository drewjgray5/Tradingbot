"""Post-edit hook: auto-format Python files with ruff."""
import json
import subprocess
import sys


def main():
    payload = json.load(sys.stdin)
    filepath = payload.get("path", "")

    if not filepath.endswith(".py"):
        print(json.dumps({}))
        return

    subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--quiet", filepath],
        capture_output=True,
        timeout=15,
    )
    subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--fix", "--quiet", filepath],
        capture_output=True,
        timeout=15,
    )

    print(json.dumps({}))


if __name__ == "__main__":
    main()
