#!/usr/bin/env python3
"""Argon2 password verification helper for mod_auth_thrive.

Called by the Prosody auth module to verify a password against a legacy
argon2 hash.  Reads a JSON file (path passed as argv[1]) containing:

    { "hash": "$argon2id$...", "password": "user_password" }

Prints "ok" on match, "fail" on mismatch, and exits.

Requirements: argon2-cffi  (pip install argon2-cffi)
"""

import json
import sys

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

_ph = PasswordHasher()


def main():
    if len(sys.argv) < 2:
        print("fail")
        sys.exit(1)

    try:
        with open(sys.argv[1]) as f:
            data = json.load(f)
        _ph.verify(data["hash"], data["password"])
        print("ok")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        print("fail")
    except Exception:
        print("fail")
        sys.exit(1)


if __name__ == "__main__":
    main()
