"""Utilities for generating CloudFront Signed Cookies."""

import base64
from datetime import UTC, datetime, timedelta
from typing import TypedDict


class CloudFrontCookies(TypedDict):
  """Type definition for the required CloudFront cookies."""

  # We use the alias (kebab-case) for the actual cookie keys
  # syntax: "CloudFront-Policy"
  CloudFront_Policy: str
  CloudFront_Signature: str
  CloudFront_Key_Pair_Id: str


def _sign_bytes(message: bytes, private_key_pem: str) -> bytes:
  """Signs a message using the RSA private key."""
  from cryptography.hazmat.primitives import hashes
  from cryptography.hazmat.primitives.asymmetric import padding, rsa
  from cryptography.hazmat.primitives.serialization import load_pem_private_key

  key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)

  # Ensure we have an RSA private key
  if not isinstance(key, rsa.RSAPrivateKey):
    raise TypeError("Expected RSA private key")

  signature = key.sign(
    message,
    padding.PKCS1v15(),
    hashes.SHA1(),
  )
  return signature


def _safe_b64(b: bytes) -> str:
  """URL-safe Base64 encoding compatible with CloudFront."""
  return b.decode("utf-8").replace("+", "-").replace("=", "_").replace("/", "~")


def create_signed_cookies(
  resource_url: str, key_pair_id: str, private_key: str, expire_minutes: int = 60
) -> dict[str, str]:
  """
  Generates the 3 CloudFront cookies required for access.

  Args:
      resource_url: The URL pattern to allow (e.g., "https://api.com/*")
      key_pair_id: The CloudFront Key ID (from Terraform)
      private_key: The PEM-encoded private key string
      expire_minutes: How long the session is valid

  Returns:
      A dictionary of cookie names to values.
  """
  expires = int((datetime.now(UTC) + timedelta(minutes=expire_minutes)).timestamp())

  # 1. Create Policy (Custom Policy allows wildcards)
  policy_json = f"""{{
        "Statement": [{{
            "Resource": "{resource_url}",
            "Condition": {{
                "DateLessThan": {{ "AWS:EpochTime": {expires} }}
            }}
        }}]
    }}"""

  # 2. Base64 Encode Policy
  policy_b64 = _safe_b64(base64.b64encode(policy_json.encode("utf-8")))

  # 3. Sign the Policy
  signature_bytes = _sign_bytes(policy_json.encode("utf-8"), private_key)
  signature_b64 = _safe_b64(base64.b64encode(signature_bytes))

  # Note: We return a plain dict instead of TypedDict to allow
  # dynamic iteration when setting cookies in FastAPI
  return {"CloudFront-Policy": policy_b64, "CloudFront-Signature": signature_b64, "CloudFront-Key-Pair-Id": key_pair_id}
