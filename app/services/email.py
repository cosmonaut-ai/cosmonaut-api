"""Email service for sending branded transactional emails via Amazon SES.

Handles world invite emails sent when a user shares a world. Verification
and password-reset emails are handled by the Cognito custom_message Lambda
trigger, so this module focuses on application-level emails only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aws_lambda_powertools import Logger

from app.core.config import settings

if TYPE_CHECKING:
  from mypy_boto3_sesv2.client import SESv2Client

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

_ses_client: SESv2Client | None = None


def _get_ses_client() -> SESv2Client:
  global _ses_client
  if _ses_client is None:
    import boto3

    _ses_client = boto3.client("sesv2", region_name=settings.AWS_REGION)
  return _ses_client


def send_world_invite(
  recipient_email: str,
  inviter_name: str,
  world_title: str,
  world_url: str,
) -> bool:
  """Send a branded invite email when a world is shared with a new user.

  Returns True on success, False on failure. Failures are logged but never
  raised — email delivery should not block the sharing operation.
  """
  if not settings.SES_FROM_EMAIL or not settings.SES_ENABLED:
    logger.warning("SES not configured; skipping invite email to %s", recipient_email)
    return False

  subject = f"{inviter_name} invited you to explore \"{world_title}\" on Cosmonaut"
  html_body = _invite_email_html(inviter_name, world_title, world_url)
  text_body = (
    f"{inviter_name} invited you to explore \"{world_title}\" on Cosmonaut.\n\n"
    f"Open this link to start your adventure:\n{world_url}\n\n"
    "— The Cosmonaut Team"
  )

  try:
    client = _get_ses_client()
    client.send_email(
      FromEmailAddress=settings.SES_FROM_EMAIL,
      Destination={"ToAddresses": [recipient_email]},
      Content={
        "Simple": {
          "Subject": {"Data": subject, "Charset": "UTF-8"},
          "Body": {
            "Html": {"Data": html_body, "Charset": "UTF-8"},
            "Text": {"Data": text_body, "Charset": "UTF-8"},
          },
        }
      },
    )
    logger.info("Sent invite email to %s for world '%s'", recipient_email, world_title)
    return True
  except Exception:
    logger.exception("Failed to send invite email to %s", recipient_email)
    return False


def _invite_email_html(inviter_name: str, world_title: str, world_url: str) -> str:
  """Return a branded HTML invite email matching Cosmonaut's space theme."""
  return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>You're invited to Cosmonaut</title>
</head>
<body style="margin:0;padding:0;background-color:#0a0a0f;font-family:'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0f;min-height:100vh;">
<tr><td align="center" style="padding:40px 16px;">

<!-- Main card -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background-color:#111118;border-radius:16px;border:1px solid #1e1e2e;overflow:hidden;">

<!-- Logo header -->
<tr><td align="center" style="padding:32px 32px 16px;">
  <table role="presentation" cellpadding="0" cellspacing="0">
  <tr>
    <td style="padding-right:10px;vertical-align:middle;">
      <div style="width:36px;height:36px;border-radius:8px;background-color:rgba(124,58,237,0.15);text-align:center;line-height:36px;font-size:20px;">&#128640;</div>
    </td>
    <td style="vertical-align:middle;">
      <span style="font-size:20px;font-weight:700;color:#f0f0f5;letter-spacing:0.5px;">Cosmonaut</span>
    </td>
  </tr>
  </table>
</td></tr>

<!-- Divider -->
<tr><td style="padding:0 32px;">
  <div style="height:1px;background:linear-gradient(90deg,transparent,#2a2a3a,transparent);"></div>
</td></tr>

<!-- Body -->
<tr><td style="padding:24px 32px 32px;">
  <p style="margin:0 0 16px;font-size:15px;color:#c8c8d4;line-height:1.6;">
    Hi there,
  </p>
  <p style="margin:0 0 8px;font-size:15px;color:#c8c8d4;line-height:1.6;">
    <strong style="color:#f0f0f5;">{inviter_name}</strong> has invited you to explore an interactive story world on Cosmonaut:
  </p>

  <!-- World title card -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0 24px;">
  <tr><td style="padding:16px 20px;background-color:#1a1a28;border:1px solid #2a2a3a;border-radius:12px;">
    <p style="margin:0;font-size:18px;font-weight:600;color:#f0f0f5;">
      &ldquo;{world_title}&rdquo;
    </p>
  </td></tr>
  </table>

  <p style="margin:0 0 24px;font-size:15px;color:#c8c8d4;line-height:1.6;">
    Every choice you make shapes the narrative. Jump in and see where the story takes you.
  </p>

  <!-- CTA button -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  <tr><td align="center">
    <a href="{world_url}" style="display:inline-block;padding:14px 32px;background-color:#7c3aed;color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;border-radius:10px;letter-spacing:0.3px;">
      Explore World
    </a>
  </td></tr>
  </table>

  <p style="margin:24px 0 0;font-size:13px;color:#888899;line-height:1.6;">
    If you don't have a Cosmonaut account yet, you can create one for free when you open the link.
  </p>
</td></tr>

</table>

<!-- Footer -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;">
<tr><td align="center" style="padding:24px 32px;">
  <p style="margin:0;font-size:12px;color:#555566;line-height:1.6;">
    You received this email because someone shared a Cosmonaut world with you.<br>
    If you weren't expecting this, you can safely ignore it.
  </p>
  <p style="margin:12px 0 0;font-size:12px;color:#444455;">
    Matson Software LLC &middot; <a href="https://cosmonaut-ai.com" style="color:#7c3aed;text-decoration:none;">cosmonaut-ai.com</a>
  </p>
</td></tr>
</table>

</td></tr>
</table>
</body>
</html>"""
