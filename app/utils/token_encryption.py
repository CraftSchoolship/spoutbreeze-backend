"""
Fernet-based token encryption for securing OAuth tokens at rest.

Usage:
    from app.utils.token_encryption import encrypt_token, decrypt_token

    ciphertext = encrypt_token("my_access_token")
    plaintext  = decrypt_token(ciphertext)
"""

from cryptography.fernet import Fernet
from app.config.settings import get_settings

_settings = get_settings()
_fernet = Fernet(_settings.token_encryption_key.encode())


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string. Returns base64-encoded ciphertext."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a token string. Returns the original plaintext."""
    return _fernet.decrypt(ciphertext.encode()).decode()
