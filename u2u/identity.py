# u2u/identity.py
# b17: U2UI1
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from cryptography.exceptions import InvalidSignature


class Identity:
    def __init__(self, private_key: Ed25519PrivateKey):
        self._private = private_key
        self._public = private_key.public_key()
        self.public_key_hex = self._public.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    @classmethod
    def generate(cls, path: Path) -> "Identity":
        key = Ed25519PrivateKey.generate()
        ident = cls(key)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        priv_bytes = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps({"version": 1, "private_key": priv_bytes.hex()}))
        return ident

    @classmethod
    def load(cls, path: Path) -> "Identity":
        try:
            data = json.loads(Path(path).read_text())
            return cls(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data["private_key"])))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Cannot load identity from {path}: {e}") from e

    @classmethod
    def load_or_generate(cls, path: Path) -> "Identity":
        path = Path(path)
        return cls.load(path) if path.exists() else cls.generate(path)

    def sign(self, message: bytes) -> str:
        return self._private.sign(message).hex()

    def verify(self, message: bytes, sig_hex: str, public_key_hex: str) -> bool:
        try:
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
            pub.verify(bytes.fromhex(sig_hex), message)
            return True
        except (InvalidSignature, ValueError):
            return False
