import hashlib


def canonicalize_text(content: str) -> bytes:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    canonical = "\n".join(lines).strip()
    return canonical.encode("utf-8")


def generate_hash(content: str) -> str:
    data = canonicalize_text(content)
    return hashlib.sha256(data).hexdigest()


def generate_hash_from_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
