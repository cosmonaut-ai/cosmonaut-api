"""Email service for sending branded transactional emails via Amazon SES.

Handles world invite emails, Stripe subscription lifecycle emails, and any
other application-level emails.  Verification and password-reset emails are
handled by the Cognito custom_message Lambda trigger.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING

from aws_lambda_powertools import Logger

from app.core.config import get_tier_limits, settings

if TYPE_CHECKING:
  from mypy_boto3_sesv2.client import SESV2Client

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

_ses_client: SESV2Client | None = None

# ---------------------------------------------------------------------------
# Brand constants (dark-theme palette — must stay in sync with the Cognito
# custom_message Lambda in cosmonaut-infra/lambdas/custom_message/index.py)
# ---------------------------------------------------------------------------
_BG = "#1a2030"
_CARD_BG = "#242d3e"
_CARD_BORDER = "#3d4d63"
_PRIMARY = "#e8c949"
_PRIMARY_FG = "#1a2030"
_FG = "#f1f4f7"
_MUTED = "#b1bfcc"
_MUTED_DARK = "#7a8a9e"
_FOOTER_TEXT = "#6b7a8e"
_DIVIDER = "#3d4d63"
_CODE_BG = "#1a2030"
_CODE_BORDER = "#e8c949"
_DESTRUCTIVE = "#e85454"
_FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"

_TIER_DISPLAY: dict[str, str] = {
  "FREE": "Free",
  "EXPLORER": "Explorer",
  "COSMONAUT": "Cosmonaut",
}


def _get_ses_client() -> SESV2Client:
  global _ses_client
  if _ses_client is None:
    import boto3

    _ses_client = boto3.client("sesv2", region_name=settings.AWS_REGION)
  return _ses_client


# ---------------------------------------------------------------------------
# Shared HTML helpers
# ---------------------------------------------------------------------------


def _logo_html() -> str:
  """Return the logo markup — an <img> from the CDN or a text fallback."""
  cdn = settings.STATIC_CONTENT_CDN_DOMAIN
  if cdn:
    return (
      f'<img src="https://{cdn}/meta/favicon.png" '
      f'alt="Cosmonaut" width="36" height="36" '
      f'style="display:block;border:0;border-radius:8px;" />'
    )
  return (
    f'<div style="width:36px;height:36px;border-radius:8px;'
    f"background-color:{_PRIMARY};text-align:center;line-height:36px;"
    f'font-size:20px;color:{_PRIMARY_FG};">C</div>'
  )


def _base_template(title: str, body_content: str, footer_note: str = "") -> str:
  """Shared branded email wrapper matching the Cosmonaut dark theme."""
  if not footer_note:
    footer_note = (
      "You received this email because of activity on your Cosmonaut account.<br>"
      "If you didn't expect this, you can safely ignore it."
    )
  return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:{_BG};font-family:{_FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="background-color:{_BG};min-height:100vh;">
<tr><td align="center" style="padding:40px 16px;">

<!-- Main card -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="max-width:480px;background-color:{_CARD_BG};border-radius:16px;
  border:1px solid {_CARD_BORDER};overflow:hidden;">

<!-- Logo header -->
<tr><td align="center" style="padding:32px 32px 16px;">
  <table role="presentation" cellpadding="0" cellspacing="0">
  <tr>
    <td style="padding-right:10px;vertical-align:middle;">
      {_logo_html()}
    </td>
    <td style="vertical-align:middle;">
      <span style="font-size:20px;font-weight:700;color:{_FG};letter-spacing:0.5px;">Cosmonaut</span>
    </td>
  </tr>
  </table>
</td></tr>

<!-- Divider -->
<tr><td style="padding:0 32px;">
  <div style="height:1px;background:linear-gradient(90deg,transparent,{_DIVIDER},transparent);"></div>
</td></tr>

<!-- Body content -->
<tr><td style="padding:24px 32px 32px;">
  {body_content}
</td></tr>

</table>

<!-- Footer -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;">
<tr><td align="center" style="padding:24px 32px;">
  <p style="margin:0;font-size:12px;color:{_FOOTER_TEXT};line-height:1.6;">
    {footer_note}
  </p>
  <p style="margin:12px 0 0;font-size:12px;color:{_FOOTER_TEXT};">
    Matson Software LLC &middot;
    <a href="https://cosmonaut-ai.com" style="color:{_PRIMARY};text-decoration:none;">cosmonaut-ai.com</a>
  </p>
</td></tr>
</table>

</td></tr>
</table>
</body>
</html>"""


def _cta_button(url: str, label: str, *, color: str = _PRIMARY, text_color: str = _PRIMARY_FG) -> str:
  """Return a centered CTA button."""
  return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  <tr><td align="center">
    <a href="{url}" style="display:inline-block;padding:14px 32px;
      background-color:{color};color:{text_color};text-decoration:none;
      font-size:15px;font-weight:600;border-radius:10px;letter-spacing:0.3px;">
      {label}
    </a>
  </td></tr>
  </table>"""


def _greeting(name: str) -> str:
  return f"Hi {name}," if name else "Hi there,"


def _frontend_url(path: str = "") -> str:
  domain = settings.FRONTEND_DOMAIN or "cosmonaut-ai.com"
  return f"https://{domain}{path}"


def _format_date(dt: datetime) -> str:
  """Format a datetime for display in emails (e.g. 'February 16, 2026')."""
  return dt.strftime("%B %d, %Y").replace(" 0", " ")


def _tier_limits_html(tier: str) -> str:
  """Return an HTML snippet listing the key limits for a tier."""
  limits = get_tier_limits(tier)
  period = "week" if limits.get("reset_days", 30) <= 7 else "month"
  return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0 24px;">
  <tr><td style="padding:16px 20px;background-color:{_CODE_BG};border:1px solid {_CARD_BORDER};border-radius:12px;">
    <p style="margin:0 0 8px;font-size:14px;font-weight:600;color:{_FG};">Your plan includes:</p>
    <p style="margin:0;font-size:14px;color:{_MUTED};line-height:1.8;">
      &bull; {limits["worlds"]} worlds per {period}<br>
      &bull; {limits["nodes"]:,} story nodes per {period}<br>
      &bull; {limits["saved_worlds"]} saved stories<br>
      &bull; {limits["audio_limit"]} audio narrations per {period}
    </p>
  </td></tr>
  </table>"""


# ---------------------------------------------------------------------------
# SES send helper
# ---------------------------------------------------------------------------


def _send_email(recipient_email: str, subject: str, html_body: str, text_body: str) -> bool:
  """Send an email via SES.  Returns True on success, False on failure.

  Failures are logged but never raised — email delivery should not block
  the calling operation.
  """
  if not settings.SES_FROM_EMAIL or not settings.SES_ENABLED:
    logger.warning("SES not configured; skipping email to %s", recipient_email)
    return False

  if not recipient_email:
    logger.warning("No recipient email provided; skipping send")
    return False

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
    logger.info("Sent email '%s' to %s", subject, recipient_email)
    return True
  except Exception:
    logger.exception("Failed to send email '%s' to %s", subject, recipient_email)
    return False


# ---------------------------------------------------------------------------
# World invite email
# ---------------------------------------------------------------------------


def send_world_invite(
  recipient_email: str,
  inviter_name: str,
  world_title: str,
  world_url: str,
) -> bool:
  """Send a branded invite email when a world is shared with a new user."""
  safe_inviter = html.escape(inviter_name)
  safe_title = html.escape(world_title)
  safe_url = html.escape(world_url)
  subject = f'{safe_inviter} invited you to explore "{safe_title}" on Cosmonaut'
  html_body = _invite_email_html(safe_inviter, safe_title, safe_url)
  text_body = (
    f'{inviter_name} invited you to explore "{world_title}" on Cosmonaut.\n\n'
    f"Open this link to start your adventure:\n{world_url}\n\n"
    "— The Cosmonaut Team"
  )
  return _send_email(recipient_email, subject, html_body, text_body)


def _invite_email_html(inviter_name: str, world_title: str, world_url: str) -> str:
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    Hi there,
  </p>
  <p style="margin:0 0 8px;font-size:15px;color:{_MUTED};line-height:1.6;">
    <strong style="color:{_FG};">{inviter_name}</strong> has invited you to explore
    an interactive story world on Cosmonaut:
  </p>

  <!-- World title card -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0 24px;">
  <tr><td style="padding:16px 20px;background-color:{_CODE_BG};border:1px solid {_CARD_BORDER};border-radius:12px;">
    <p style="margin:0;font-size:18px;font-weight:600;color:{_FG};">
      &ldquo;{world_title}&rdquo;
    </p>
  </td></tr>
  </table>

  <p style="margin:0 0 24px;font-size:15px;color:{_MUTED};line-height:1.6;">
    Every choice you make shapes the narrative. Jump in and see where the story takes you.
  </p>

  {_cta_button(world_url, "Explore World")}

  <p style="margin:24px 0 0;font-size:13px;color:{_MUTED_DARK};line-height:1.6;">
    If you don't have a Cosmonaut account yet, you can create one for free when you open the link.
  </p>"""
  return _base_template(
    "You're invited to Cosmonaut",
    body,
    footer_note=(
      "You received this email because someone shared a Cosmonaut world with you.<br>"
      "If you weren't expecting this, you can safely ignore it."
    ),
  )


# ---------------------------------------------------------------------------
# Stripe subscription emails
# ---------------------------------------------------------------------------


def send_subscription_welcome(recipient_email: str, name: str, tier: str) -> bool:
  """Welcome email after a new subscription purchase via Stripe Checkout."""
  safe_name = html.escape(name)
  display = _TIER_DISPLAY.get(tier, tier.title())
  subject = f"Welcome to the {display} plan on Cosmonaut!"
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    {_greeting(safe_name)}
  </p>
  <p style="margin:0 0 8px;font-size:15px;color:{_MUTED};line-height:1.6;">
    Your subscription to the <strong style="color:{_FG};">{display}</strong> plan is now active.
    Thank you for supporting Cosmonaut!
  </p>

  {_tier_limits_html(tier)}

  <p style="margin:0 0 24px;font-size:15px;color:{_MUTED};line-height:1.6;">
    You can manage your subscription anytime from the pricing page.
  </p>

  {_cta_button(_frontend_url("/pricing"), "View Your Plan")}"""

  text_body = (
    f"{_greeting(name)}\n\n"
    f"Your subscription to the {display} plan is now active. "
    f"Thank you for supporting Cosmonaut!\n\n"
    f"You can manage your subscription at {_frontend_url('/pricing')}\n\n"
    "— The Cosmonaut Team"
  )
  return _send_email(recipient_email, subject, _base_template(subject, body), text_body)


def send_subscription_renewed(recipient_email: str, name: str, tier: str) -> bool:
  """Confirmation email after a successful subscription renewal payment."""
  safe_name = html.escape(name)
  display = _TIER_DISPLAY.get(tier, tier.title())
  subject = f"Your {display} subscription has been renewed"
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    {_greeting(safe_name)}
  </p>
  <p style="margin:0 0 24px;font-size:15px;color:{_MUTED};line-height:1.6;">
    Your <strong style="color:{_FG};">{display}</strong> plan has been successfully renewed
    and your usage limits have been reset. You're all set for another billing period!
  </p>

  {_cta_button(_frontend_url("/dashboard"), "Go to Dashboard")}"""

  text_body = (
    f"{_greeting(name)}\n\n"
    f"Your {display} plan has been successfully renewed and your usage "
    f"limits have been reset.\n\n"
    f"Go to your dashboard: {_frontend_url('/dashboard')}\n\n"
    "— The Cosmonaut Team"
  )
  return _send_email(recipient_email, subject, _base_template(subject, body), text_body)


def send_payment_failed(recipient_email: str, name: str) -> bool:
  """Alert email when a subscription invoice payment fails."""
  safe_name = html.escape(name)
  subject = "Action required: your Cosmonaut payment failed"
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    {_greeting(safe_name)}
  </p>
  <p style="margin:0 0 24px;font-size:15px;color:{_MUTED};line-height:1.6;">
    We were unable to process your latest subscription payment. Your access
    remains active while we retry, but please update your payment method to avoid interruption.
  </p>

  {_cta_button(_frontend_url("/pricing"), "Update Payment Method")}

  <p style="margin:24px 0 0;font-size:13px;color:{_MUTED_DARK};line-height:1.6;">
    If you believe this is an error, please check with your bank or
    card issuer. We'll automatically retry the charge over the next few days.
  </p>"""

  text_body = (
    f"{_greeting(name)}\n\n"
    "We were unable to process your latest subscription payment. "
    "Please update your payment method to avoid interruption.\n\n"
    f"Update payment: {_frontend_url('/pricing')}\n\n"
    "— The Cosmonaut Team"
  )
  return _send_email(recipient_email, subject, _base_template(subject, body), text_body)


def send_subscription_cancellation_scheduled(
  recipient_email: str,
  name: str,
  cancel_date: datetime,
) -> bool:
  """Confirmation email when a subscription cancellation is scheduled."""
  safe_name = html.escape(name)
  formatted_date = _format_date(cancel_date)
  subject = "Your Cosmonaut subscription cancellation is confirmed"
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    {_greeting(safe_name)}
  </p>
  <p style="margin:0 0 8px;font-size:15px;color:{_MUTED};line-height:1.6;">
    Your subscription cancellation has been confirmed. You'll continue to have full access
    to your current plan until <strong style="color:{_FG};">{formatted_date}</strong>.
  </p>
  <p style="margin:0 0 24px;font-size:15px;color:{_MUTED};line-height:1.6;">
    After that date, your account will revert to the Free plan. Changed your mind? You can resubscribe anytime.
  </p>

  {_cta_button(_frontend_url("/pricing"), "Resubscribe")}"""

  text_body = (
    f"{_greeting(name)}\n\n"
    f"Your subscription cancellation has been confirmed. You'll continue "
    f"to have full access until {formatted_date}.\n\n"
    f"After that, your account will revert to the Free plan.\n\n"
    f"Resubscribe: {_frontend_url('/pricing')}\n\n"
    "— The Cosmonaut Team"
  )
  return _send_email(recipient_email, subject, _base_template(subject, body), text_body)


def send_subscription_plan_change_scheduled(
  recipient_email: str,
  name: str,
  pending_tier: str,
  effective_date: datetime,
) -> bool:
  """Email when a plan change (typically a downgrade) is scheduled for period end."""
  safe_name = html.escape(name)
  display = _TIER_DISPLAY.get(pending_tier, pending_tier.title())
  formatted_date = _format_date(effective_date)
  subject = f"Your Cosmonaut plan change to {display} is scheduled"
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    {_greeting(safe_name)}
  </p>
  <p style="margin:0 0 8px;font-size:15px;color:{_MUTED};line-height:1.6;">
    Your plan change to <strong style="color:{_FG};">{display}</strong> has been scheduled.
    You'll keep your current plan benefits
    until <strong style="color:{_FG};">{formatted_date}</strong>,
    and the change will take effect automatically.
  </p>

  {_tier_limits_html(pending_tier)}

  <p style="margin:0 0 24px;font-size:15px;color:{_MUTED};line-height:1.6;">
    If you change your mind, you can update your plan from the pricing page before the switch.
  </p>

  {_cta_button(_frontend_url("/pricing"), "Manage Plan")}"""

  text_body = (
    f"{_greeting(name)}\n\n"
    f"Your plan change to {display} has been scheduled and will take "
    f"effect on {formatted_date}.\n\n"
    f"Manage your plan: {_frontend_url('/pricing')}\n\n"
    "— The Cosmonaut Team"
  )
  return _send_email(recipient_email, subject, _base_template(subject, body), text_body)


def send_subscription_ended(recipient_email: str, name: str) -> bool:
  """Email when a subscription is fully deleted (end of cancellation period or payment failure)."""
  safe_name = html.escape(name)
  subject = "Your Cosmonaut subscription has ended"
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    {_greeting(safe_name)}
  </p>
  <p style="margin:0 0 8px;font-size:15px;color:{_MUTED};line-height:1.6;">
    Your paid subscription has ended and your account has been moved to the
    <strong style="color:{_FG};">Free</strong> plan.
  </p>
  <p style="margin:0 0 24px;font-size:15px;color:{_MUTED};line-height:1.6;">
    You can still access Cosmonaut with Free-tier limits. If you'd like to pick up
    where you left off, you can resubscribe anytime.
  </p>

  {_cta_button(_frontend_url("/pricing"), "View Plans")}

  <p style="margin:24px 0 0;font-size:13px;color:{_MUTED_DARK};line-height:1.6;">
    Your saved stories are still here &mdash; they won't be deleted.
  </p>"""

  text_body = (
    f"{_greeting(name)}\n\n"
    "Your paid subscription has ended and your account has been moved to "
    "the Free plan.\n\n"
    "Your saved stories are still here. You can resubscribe anytime.\n\n"
    f"View plans: {_frontend_url('/pricing')}\n\n"
    "— The Cosmonaut Team"
  )
  return _send_email(recipient_email, subject, _base_template(subject, body), text_body)


# ---------------------------------------------------------------------------
# User feedback email (sent to support)
# ---------------------------------------------------------------------------

_CATEGORY_DISPLAY: dict[str, str] = {
  "bug": "Bug Report",
  "feature": "Feature Request",
  "feedback": "General Feedback",
  "other": "Other",
}


def send_feedback_email(
  user_email: str,
  user_id: str,
  tier: str,
  category: str,
  message: str,
) -> bool:
  """Send a formatted feedback email to the support address."""
  safe_email = html.escape(user_email)
  safe_user_id = html.escape(user_id)
  safe_message = html.escape(message)
  display_category = _CATEGORY_DISPLAY.get(category, category.title())
  display_tier = _TIER_DISPLAY.get(tier, tier.title())

  subject = f"[{display_category}] Feedback from {user_email}"
  body = f"""
  <p style="margin:0 0 16px;font-size:15px;color:{_MUTED};line-height:1.6;">
    New feedback submission:
  </p>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
  <tr><td style="padding:16px 20px;background-color:{_CODE_BG};border:1px solid {_CARD_BORDER};border-radius:12px;">
    <p style="margin:0 0 8px;font-size:14px;color:{_MUTED};">
      <strong style="color:{_FG};">From:</strong> {safe_email}
    </p>
    <p style="margin:0 0 8px;font-size:14px;color:{_MUTED};">
      <strong style="color:{_FG};">User ID:</strong> {safe_user_id}
    </p>
    <p style="margin:0 0 8px;font-size:14px;color:{_MUTED};">
      <strong style="color:{_FG};">Tier:</strong> {display_tier}
    </p>
    <p style="margin:0;font-size:14px;color:{_MUTED};">
      <strong style="color:{_FG};">Category:</strong> {display_category}
    </p>
  </td></tr>
  </table>

  <p style="margin:0 0 8px;font-size:14px;font-weight:600;color:{_FG};">Message:</p>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  <tr><td style="padding:16px 20px;background-color:{_CODE_BG};border:1px solid {_CARD_BORDER};border-radius:12px;">
    <p style="margin:0;font-size:14px;color:{_MUTED};line-height:1.6;white-space:pre-wrap;">{safe_message}</p>
  </td></tr>
  </table>"""

  text_body = (
    f"New feedback submission\n\n"
    f"From: {user_email}\n"
    f"User ID: {user_id}\n"
    f"Tier: {display_tier}\n"
    f"Category: {display_category}\n\n"
    f"Message:\n{message}\n"
  )

  html_body = _base_template(f"Feedback: {display_category}", body)
  return _send_email(settings.SES_SUPPORT_EMAIL, subject, html_body, text_body)
