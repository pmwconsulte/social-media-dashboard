"""Tenant-safe WhatsApp phone-number ownership checks.

This module deliberately does not provision credentials or infer ownership from
an environment variable. A send is permitted only when the configured number
has an explicit database mapping to the authenticated workspace.
"""


class WhatsAppTenantConfigurationError(RuntimeError):
    """Raised when a WhatsApp number is not safely bound to a workspace."""


def resolve_workspace_phone_number(
    *,
    configured_phone_id,
    mapped_phone_id,
    mapped_workspace_id,
    workspace_id,
    inbound_phone_id=None,
    configured_workspace_slug=None,
    requested_workspace_slug=None,
):
    """Return the configured phone ID only after strict tenant ownership checks."""
    configured = str(configured_phone_id or "").strip()
    mapped = str(mapped_phone_id or "").strip()
    inbound = str(inbound_phone_id or "").strip()
    configured_slug = str(configured_workspace_slug or "").strip()
    requested_slug = str(requested_workspace_slug or "").strip()

    if not configured:
        raise WhatsAppTenantConfigurationError(
            "WHATSAPP_PHONE_NUMBER_ID não está configurado."
        )
    if not configured_slug or not requested_slug or configured_slug != requested_slug:
        raise WhatsAppTenantConfigurationError(
            "A Cloud API global só pode ser utilizada pelo workspace explicitamente configurado."
        )
    if not mapped or mapped != configured:
        raise WhatsAppTenantConfigurationError(
            "O número WhatsApp não tem um vínculo explícito e válido com este workspace."
        )
    if mapped_workspace_id is None or workspace_id is None:
        raise WhatsAppTenantConfigurationError(
            "Não foi possível confirmar a propriedade do número WhatsApp."
        )
    if str(mapped_workspace_id) != str(workspace_id):
        raise WhatsAppTenantConfigurationError(
            "O número WhatsApp pertence a outro workspace. Envio bloqueado."
        )
    if inbound and inbound != configured:
        raise WhatsAppTenantConfigurationError(
            "O número que recebeu a mensagem não corresponde ao número autorizado deste workspace."
        )
    return configured
