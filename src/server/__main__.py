"""Small deployment helpers; production HTTP is served by Gunicorn."""
import argparse
from getpass import getpass

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['hash-password'])
    parser.parse_args()
    password = getpass('Admin password: ')
    if len(password) < 12:
        parser.error('Use a password with at least 12 characters.')
    if password != getpass('Confirm password: '):
        parser.error('Passwords do not match.')
    print(generate_password_hash(password))


if __name__ == '__main__':
    main()
