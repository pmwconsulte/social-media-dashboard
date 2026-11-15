import unittest

from whatsapp_tenant import (
    WhatsAppTenantConfigurationError,
    resolve_workspace_phone_number,
)


class ResolveWorkspacePhoneNumberTests(unittest.TestCase):
    def test_allows_only_explicitly_mapped_number_for_configured_workspace(self):
        phone_id = resolve_workspace_phone_number(
            configured_phone_id="123456",
            mapped_phone_id="123456",
            mapped_workspace_id="workspace-a",
            workspace_id="workspace-a",
            inbound_phone_id="123456",
            configured_workspace_slug="pmw-consultoria",
            requested_workspace_slug="pmw-consultoria",
        )
        self.assertEqual(phone_id, "123456")

    def test_blocks_unmapped_number(self):
        with self.assertRaises(WhatsAppTenantConfigurationError):
            resolve_workspace_phone_number(
                configured_phone_id="123456",
                mapped_phone_id=None,
                mapped_workspace_id=None,
                workspace_id="workspace-a",
                configured_workspace_slug="pmw-consultoria",
                requested_workspace_slug="pmw-consultoria",
            )

    def test_blocks_number_owned_by_another_workspace(self):
        with self.assertRaises(WhatsAppTenantConfigurationError):
            resolve_workspace_phone_number(
                configured_phone_id="123456",
                mapped_phone_id="123456",
                mapped_workspace_id="workspace-b",
                workspace_id="workspace-a",
                configured_workspace_slug="pmw-consultoria",
                requested_workspace_slug="pmw-consultoria",
            )

    def test_blocks_other_workspace_even_if_database_mapping_is_wrongly_reassigned(self):
        with self.assertRaises(WhatsAppTenantConfigurationError):
            resolve_workspace_phone_number(
                configured_phone_id="123456",
                mapped_phone_id="123456",
                mapped_workspace_id="workspace-b",
                workspace_id="workspace-b",
                configured_workspace_slug="pmw-consultoria",
                requested_workspace_slug="customer-b",
            )

    def test_blocks_inbound_message_from_different_phone_number(self):
        with self.assertRaises(WhatsAppTenantConfigurationError):
            resolve_workspace_phone_number(
                configured_phone_id="123456",
                mapped_phone_id="123456",
                mapped_workspace_id="workspace-a",
                workspace_id="workspace-a",
                inbound_phone_id="999999",
                configured_workspace_slug="pmw-consultoria",
                requested_workspace_slug="pmw-consultoria",
            )

    def test_blocks_missing_workspace_binding(self):
        with self.assertRaises(WhatsAppTenantConfigurationError):
            resolve_workspace_phone_number(
                configured_phone_id="123456",
                mapped_phone_id="123456",
                mapped_workspace_id="workspace-a",
                workspace_id="workspace-a",
                configured_workspace_slug="",
                requested_workspace_slug="pmw-consultoria",
            )


if __name__ == "__main__":
    unittest.main()
