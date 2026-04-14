"""Pre-shell hook: flag dangerous commands that touch production or live trading."""
import json
import re
import sys

DANGEROUS_PATTERNS = [
    r"LIVE_TRADING_KILL_SWITCH\s*=\s*(false|0|no|off)",
    r"--force",
    r"DROP\s+(TABLE|DATABASE|SCHEMA)",
    r"DELETE\s+FROM\s+\w+\s*;?\s*$",
    r"TRUNCATE\s+",
    r"rm\s+-rf\s+/",
    r"alembic\s+downgrade",
    r"git\s+push\s+.*--force",
]


def main():
    payload = json.load(sys.stdin)
    command = payload.get("command", "")

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            print(json.dumps({
                "permission": "ask",
                "user_message": f"This command matches a safety pattern: {pattern}. Please review before continuing.",
                "agent_message": "A safety hook flagged this command as potentially dangerous.",
            }))
            return

    print(json.dumps({"permission": "allow"}))


if __name__ == "__main__":
    main()
