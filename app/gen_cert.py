"""Generate a self-signed TLS certificate for local HTTPS testing."""

import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

cert_dir = Path(__file__).parent / "certs"
cert_dir.mkdir(exist_ok=True)

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "EcoScout Local Dev"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
    .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv4Address("172.20.10.3")),
        ]),
        critical=False,
    )
    .sign(key, hashes.SHA256())
)

key_path = cert_dir / "key.pem"
cert_path = cert_dir / "cert.pem"

key_path.write_bytes(
    key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
)
cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

print(f"Generated:\n  {cert_path}\n  {key_path}")
