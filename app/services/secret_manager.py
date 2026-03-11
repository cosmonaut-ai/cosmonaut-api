from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

from cachetools import TTLCache, cached

from app.core.config import settings

if TYPE_CHECKING:
  from mypy_boto3_ssm.client import SSMClient
  from mypy_boto3_ssm.type_defs import GetParameterResultTypeDef

ssm_client: SSMClient | None = None


def get_ssm_client() -> SSMClient:
  global ssm_client
  if ssm_client is None:
    import boto3

    ssm_client = boto3.client("ssm", region_name=settings.AWS_REGION)
  return ssm_client


_secret_cache: TTLCache[str, str] = TTLCache(maxsize=64, ttl=600)
_cache_lock = threading.Lock()


@cached(cache=_secret_cache, lock=_cache_lock)
def get_secret_value(param_path: str) -> str:
  """
  Fetches a secret/parameter value from AWS SSM Parameter Store.
  Attempts to use the AWS Parameters and Secrets Lambda Extension if available,
  otherwise falls back to the boto3 SSM client.
  """
  # Try using the AWS Parameters and Secrets Lambda Extension first
  extension_port = os.environ.get("SSM_PARAMETER_STORE_HTTP_PORT", "2773")
  aws_session_token = os.environ.get("AWS_SESSION_TOKEN")

  if aws_session_token:
    try:
      # extension URL: http://localhost:PORT/systemsmanager/parameters/get/?name=PARAMETER_NAME&withDecryption=true
      encoded_path = urllib.parse.quote(param_path)
      url = f"http://localhost:{extension_port}/systemsmanager/parameters/get/?name={encoded_path}&withDecryption=true"

      req = urllib.request.Request(url)
      req.add_header("X-Aws-Parameters-Secrets-Token", aws_session_token)

      with urllib.request.urlopen(req, timeout=2) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        val = data.get("Parameter", {}).get("Value")
        if val is not None:
          return str(val)
    except Exception:
      # Fallback to boto3 if extension fails or is not available
      pass

  response: GetParameterResultTypeDef = get_ssm_client().get_parameter(Name=param_path, WithDecryption=True)
  if not response["Parameter"] or not response["Parameter"].get("Value"):
    raise ValueError(f"Parameter {param_path} not found")
  return response["Parameter"].get("Value") or ""
