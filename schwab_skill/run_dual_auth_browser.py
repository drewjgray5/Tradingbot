#!/usr/bin/env python3
"""
OAuth with local callback server - no copy/paste. Browser redirects to us; we capture automatically.

STEP 1: Add callback URL in Schwab Developer Portal for BOTH apps:
  https://127.0.0.1:8182
  (My Apps -> your app -> App Details -> Callback URL - add this one)

STEP 2: Run: python run_dual_auth_browser.py

You'll get a browser security warning (self-signed cert) - click Advanced -> Proceed.
"""
import datetime
import os
import ssl
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

CALLBACK_PORT = 8182
CALLBACK_URL = f"https://127.0.0.1:{CALLBACK_PORT}"

_captured = {"code": None, "error": None}


class OAuthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "code" in qs:
            _captured["code"] = qs["code"][0]
            body = b"<html><body><h1>Success!</h1><p>Close this window and check the terminal.</p></body></html>"
        elif "error" in qs:
            _captured["error"] = qs.get("error_description", qs.get("error", [b"Unknown"]))[0]
            if isinstance(_captured["error"], bytes):
                _captured["error"] = _captured["error"].decode("utf-8", "replace")
            body = f"<html><body><h1>Error</h1><p>{_captured['error']}</p></body></html>".encode()
        else:
            body = b"<html><body><h1>No code. Check callback URL matches Schwab app settings.</h1></body></html>"

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _make_cert():
    """Create self-signed cert with cryptography (already a dependency)."""
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(65537, 2048, default_backend())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    san = x509.SubjectAlternativeName([x509.DNSName("127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256(), default_backend())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    cert_path = SKILL_DIR / "localhost.pem"
    key_path = SKILL_DIR / "localhost-key.pem"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    return cert_path, key_path


def _run_session(session, name: str) -> bool:
    _captured["code"] = None
    _captured["error"] = None

    # Temporarily override redirect for our server
    orig = session.redirect_uri
    session.redirect_uri = CALLBACK_URL

    auth_url = session.get_authorization_url()
    print(f"\n--- {name} ---")
    print("Opening browser. Log in to Schwab, approve. Accept cert warning if prompted.")
    webbrowser.open(auth_url)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert_path, key_path = _make_cert()
    context.load_cert_chain(str(cert_path), str(key_path))

    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), OAuthHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    # Keep handling until we get OAuth callback (ignore favicon, etc.)
    while _captured["code"] is None and _captured["error"] is None:
        server.handle_request()
    server.server_close()

    session.redirect_uri = orig

    if _captured["error"]:
        print(f"OAuth error: {_captured['error']}")
        return False
    if _captured["code"]:
        session.complete_auth(_captured["code"])
        print("  Saved.")
        return True
    print("  No code received.")
    return False


def main():
    print("Callback server will use:", CALLBACK_URL)
    print("Ensure this is added to BOTH Schwab apps in Developer Portal.")
    if "--wait" in sys.argv:
        input("Press Enter to start...")

    os.environ["SCHWAB_CALLBACK_URL"] = CALLBACK_URL

    from schwab_auth import DualSchwabAuth
    auth = DualSchwabAuth(skill_dir=SKILL_DIR)
    auth.market_session.redirect_uri = CALLBACK_URL
    auth.account_session.redirect_uri = CALLBACK_URL

    ok1 = _run_session(auth.market_session, "MARKET SESSION")
    ok2 = _run_session(auth.account_session, "ACCOUNT SESSION")

    if ok1 and ok2:
        print("\nDone. Both sessions saved.")
        # Update .env so future refreshes use this callback
        env_path = SKILL_DIR / ".env"
        txt = env_path.read_text()
        if CALLBACK_URL not in txt:
            txt = txt.replace("SCHWAB_CALLBACK_URL=https://127.0.0.1", f"SCHWAB_CALLBACK_URL={CALLBACK_URL}")
            env_path.write_text(txt)
    else:
        print("\nOne or more failed. See TROUBLESHOOTING.md")


if __name__ == "__main__":
    main()
