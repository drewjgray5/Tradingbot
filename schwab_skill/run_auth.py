#!/usr/bin/env python3
"""
One-time OAuth setup. Run from the schwab_skill directory:
  python run_auth.py

Prints the auth URL; after you log in and get redirected, paste the
full redirect URL when prompted.
"""
import sys
from pathlib import Path

# Ensure skill dir is on path
SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

from auth import SchwabAuth


def main():
    auth = SchwabAuth(skill_dir=SKILL_DIR)
    url = auth.get_authorization_url()
    print("Open this URL in your browser, log in, and approve:")
    print(url)
    print()
    redirect = input("Paste the full redirect URL (or auth code): ").strip()
    if not redirect:
        print("No input. Exiting.")
        sys.exit(1)
    auth.complete_initial_auth(redirect)
    print("Auth complete. Tokens saved to tokens.enc")

if __name__ == "__main__":
    main()
