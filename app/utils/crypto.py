import base64

from nacl.public import PrivateKey

from app.extensions import get_fernet


def generate_keypair():
    private_key = PrivateKey.generate()
    private_b64 = base64.b64encode(bytes(private_key)).decode()
    public_b64 = base64.b64encode(bytes(private_key.public_key)).decode()
    return public_b64, private_b64


def derive_public_key(private_b64):
    raw = base64.b64decode(private_b64)
    private_key = PrivateKey(raw)
    return base64.b64encode(bytes(private_key.public_key)).decode()


def is_valid_wg_key(value):
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        return False
    return len(raw) == 32


def keypair_matches(public_b64, private_b64):
    try:
        return derive_public_key(private_b64) == public_b64
    except Exception:
        return False


def encrypt_private_key(private_b64):
    return get_fernet().encrypt(private_b64.encode()).decode()


def decrypt_private_key(token):
    return get_fernet().decrypt(token.encode()).decode()
