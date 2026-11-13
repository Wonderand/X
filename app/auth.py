import argparse
import base64
import getpass
import hashlib
import hmac
from pathlib import Path
import secrets


def hash_password(password):
    if len(password) < 12:
        raise ValueError('Password must contain at least 12 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return 'scrypt$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def verify_password(password, encoded):
    try:
        _, salt, expected = encoded.split('$')
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description='Initialize local administrator credentials')
    parser.add_argument('command', choices=['init'])
    parser.parse_args()
    from dotenv import dotenv_values, set_key
    path = Path('.env')
    current = dotenv_values(path) if path.exists() else {}
    if current.get('XOPS_ADMIN_PASSWORD_HASH') or current.get('XOPS_SESSION_SECRET'):
        raise SystemExit('Administrator settings already exist; edit .env explicitly to rotate them.')
    password = getpass.getpass('Administrator password (at least 12 characters): ')
    if password != getpass.getpass('Repeat password: '):
        raise SystemExit('Passwords do not match')
    digest = hash_password(password)
    if not path.exists():
        example = Path('.env.example')
        path.write_text(example.read_text(encoding='utf-8') if example.exists() else '', encoding='utf-8')
    set_key(str(path), 'XOPS_ADMIN_PASSWORD_HASH', digest)
    set_key(str(path), 'XOPS_SESSION_SECRET', secrets.token_urlsafe(48))
    print('Local administrator configured in .env. Start the web service next.')


if __name__ == '__main__':
    main()
