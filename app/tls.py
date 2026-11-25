import os
import ssl
from pathlib import Path

import truststore


def system_verify(ca_file: Path | None = None) -> ssl.SSLContext | bool:
    """Use platform trust or a dedicated CA pinned by the caller."""
    if ca_file is not None:
        return ssl.create_default_context(cafile=str(ca_file))
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
