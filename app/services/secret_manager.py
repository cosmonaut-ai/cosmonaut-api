from functools import lru_cache

import boto3
from mypy_boto3_ssm.client import SSMClient
from mypy_boto3_ssm.type_defs import GetParameterResultTypeDef

from app.core.config import settings

ssm_client: SSMClient | None = None


def get_ssm_client() -> SSMClient:
  global ssm_client
  if ssm_client is None:
    ssm_client = boto3.client("ssm", region_name=settings.AWS_REGION)
  return ssm_client


@lru_cache(maxsize=None)
def get_secret_value(param_path: str) -> str:
  response: GetParameterResultTypeDef = get_ssm_client().get_parameter(Name=param_path, WithDecryption=True)
  if not response["Parameter"] or not response["Parameter"].get("Value"):
    raise ValueError(f"Parameter {param_path} not found")
  return response["Parameter"].get("Value") or ""
