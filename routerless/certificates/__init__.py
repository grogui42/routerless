"""Built-in SSL certificate bundles."""
from pathlib import Path

# Path to the Freebox Root CA certificate bundle
FREEBOX_CA_BUNDLE = Path(__file__).parent / "freebox-ca-bundle.pem"
