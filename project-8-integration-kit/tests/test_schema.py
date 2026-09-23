"""Normalisation and merge: the part that decides what the rest of the system
believes about a person."""

import unittest

from integration_kit.connectors.billing import BillingWebhookConnector
from integration_kit.connectors.crm import CrmConnector
from integration_kit.connectors.helpdesk import HelpdeskConnector
from integration_kit.schema import (
    Contact,
    merge,
    merge_all,
    normalise_email,
    normalise_phone,
    stable_contact_id,
    to_utc_iso,
)
from tests.fakes import canned_billing_event, canned_crm_pages, canned_helpdesk_pages


class TestNormalisers(unittest.TestCase):
    def test_email_lowercased_and_trimmed(self):
        self.assertEqual(normalise_email("  Ada.Lovelace@Example.COM "), "ada.lovelace@example.com")

    def test_invalid_emails_become_none(self):
        for bad in ("", None, "not-an-email", "a@b", "a b@c.com", "@example.com"):
            with self.subTest(value=bad):
                self.assertIsNone(normalise_email(bad))

    def test_plus_tags_and_dots_are_preserved(self):
        """Gmail-specific canonicalisation applied universally merges two
        genuinely different people. A duplicate is cheaper to fix than a
        false merge."""
        self.assertEqual(normalise_email("a.b+tag@fastmail.com"), "a.b+tag@fastmail.com")

    def test_phone_national_gets_default_country(self):
        self.assertEqual(normalise_phone("0400 123 456"), "+61400123456")

    def test_phone_already_international_is_kept(self):
        self.assertEqual(normalise_phone("+61 2 9000 1111"), "+61290001111")

    def test_phone_punctuation_stripped(self):
        self.assertEqual(normalise_phone("(02) 9000-1111", default_country="+61"), "+61290001111")

    def test_empty_phone_is_none(self):
        for bad in ("", None, "   ", "n/a"):
            with self.subTest(value=bad):
                self.assertIsNone(normalise_phone(bad))

    def test_parenthesised_trunk_prefix_is_dropped(self):
        """`+61 (0) 400 ...` means "omit this 0 when dialling
        internationally". Stripping punctuation blindly gives
        `+610400000000` -- an extra digit, and a number that never connects.

        Found by probing the function after the suite was already green, which
        is the argument for poking at edge cases by hand as well as asserting
        the ones you thought of.
        """
        self.assertEqual(normalise_phone("+61 (0) 400 000 000"), "+61400000000")
        self.assertEqual(normalise_phone("+44 (0)20 7123 4567"), "+442071234567")

    def test_short_junk_is_none_not_a_bare_country_code(self):
        """`"0"` used to normalise to `"+61"`: a country code with no number,
        which then sits in the CRM looking like real data."""
        for junk in ("0", "00", "+", "+0", "12345", "+1234"):
            with self.subTest(value=junk):
                self.assertIsNone(normalise_phone(junk))

    def test_a_genuine_short_national_number_still_works(self):
        self.assertEqual(normalise_phone("02 9000 1111"), "+61290001111")

    def test_timestamps_from_three_shapes_agree(self):
        seconds = to_utc_iso(1735689600)
        millis = to_utc_iso(1735689600000)
        iso_z = to_utc_iso("2025-01-01T00:00:00Z")
        self.assertEqual(seconds, millis)
        self.assertEqual(seconds, iso_z)
        self.assertTrue(seconds.endswith("+00:00"))

    def test_naive_datetime_string_is_assumed_utc(self):
        self.assertEqual(to_utc_iso("2025-01-01T00:00:00"), "2025-01-01T00:00:00+00:00")

    def test_offset_datetime_is_converted_not_truncated(self):
        self.assertEqual(to_utc_iso("2025-01-01T10:00:00+10:00"), "2025-01-01T00:00:00+00:00")

    def test_garbage_timestamp_is_none_not_an_exception(self):
        self.assertIsNone(to_utc_iso("last tuesday"))


class TestStableIds(unittest.TestCase):
    def test_same_email_different_source_gives_same_id(self):
        """This is what makes the merge work at all."""
        a = stable_contact_id("ada@example.com", "crm", "people/c1")
        b = stable_contact_id("ada@example.com", "helpdesk", "9001")
        self.assertEqual(a, b)

    def test_id_is_stable_across_runs(self):
        self.assertEqual(
            stable_contact_id("ada@example.com", "crm", "x"),
            stable_contact_id("ada@example.com", "crm", "x"),
        )

    def test_no_email_falls_back_to_source_scoped_id(self):
        a = stable_contact_id(None, "helpdesk", "9002")
        b = stable_contact_id(None, "crm", "9002")
        self.assertNotEqual(a, b, "sourceless ids must not collide across systems")


class TestConnectorMapping(unittest.TestCase):
    def test_crm_record_maps(self):
        record = canned_crm_pages()[0]["contacts"][0]
        contact = CrmConnector.to_contact(record)
        self.assertEqual(contact.email, "ada.lovelace@example.com")
        self.assertEqual(contact.full_name, "Ada Lovelace")
        self.assertEqual(contact.phone, "+61290001111")
        self.assertEqual(contact.company, "Analytical Engines Pty Ltd")
        self.assertEqual(contact.source, "crm")
        self.assertEqual(contact.raw, record, "raw payload must be retained")

    def test_helpdesk_record_maps(self):
        record = canned_helpdesk_pages()[0]["results"][0]
        contact = HelpdeskConnector.to_contact(record)
        self.assertEqual(contact.email, "ada.lovelace@example.com")
        self.assertEqual(contact.full_name, "Ada")
        self.assertIsNone(contact.company, "empty company name must normalise to None")

    def test_billing_event_maps(self):
        contact = BillingWebhookConnector.to_contact(canned_billing_event())
        self.assertEqual(contact.email, "grace@example.com")
        self.assertEqual(contact.full_name, "Grace B. Hopper")
        self.assertEqual(contact.tags, ("billing:aud",))

    def test_all_three_produce_the_same_field_set(self):
        """The whole point of the layer: downstream never learns the source."""
        contacts = [
            CrmConnector.to_contact(canned_crm_pages()[0]["contacts"][0]),
            HelpdeskConnector.to_contact(canned_helpdesk_pages()[0]["results"][0]),
            BillingWebhookConnector.to_contact(canned_billing_event()),
        ]
        keysets = {tuple(sorted(c.to_dict())) for c in contacts}
        self.assertEqual(len(keysets), 1)


def contact(source, **kwargs):
    base = dict(
        contact_id="ct_x",
        email="ada@example.com",
        full_name=None,
        phone=None,
        company=None,
        source=source,
        source_id="1",
        created_at=None,
        updated_at=None,
        tags=(),
    )
    base.update(kwargs)
    return Contact(**base)


class TestMerge(unittest.TestCase):
    def test_more_trusted_source_wins_a_conflict(self):
        crm = contact("crm", full_name="Ada Lovelace")
        helpdesk = contact("helpdesk", full_name="ada l")
        self.assertEqual(merge(crm, helpdesk).full_name, "Ada Lovelace")
        self.assertEqual(merge(helpdesk, crm).full_name, "Ada Lovelace", "merge must be order-independent")

    def test_less_trusted_source_still_fills_a_gap(self):
        crm = contact("crm", full_name="Ada Lovelace")
        helpdesk = contact("helpdesk", phone="+61400999000")
        self.assertEqual(merge(crm, helpdesk).phone, "+61400999000")

    def test_present_value_is_never_overwritten_by_absent(self):
        crm = contact("crm", company="Analytical Engines")
        helpdesk = contact("helpdesk", company=None)
        self.assertEqual(merge(crm, helpdesk).company, "Analytical Engines")

    def test_recency_breaks_a_same_trust_tie(self):
        a = contact("crm", full_name="Old", updated_at="2025-01-01T00:00:00+00:00")
        b = contact("crm", full_name="New", updated_at="2026-01-01T00:00:00+00:00")
        self.assertEqual(merge(a, b).full_name, "New")

    def test_created_at_takes_the_earliest_updated_at_the_latest(self):
        a = contact("crm", created_at="2025-01-01T00:00:00+00:00", updated_at="2025-06-01T00:00:00+00:00")
        b = contact("helpdesk", created_at="2024-01-01T00:00:00+00:00", updated_at="2026-06-01T00:00:00+00:00")
        merged = merge(a, b)
        self.assertEqual(merged.created_at, "2024-01-01T00:00:00+00:00")
        self.assertEqual(merged.updated_at, "2026-06-01T00:00:00+00:00")

    def test_tags_are_unioned_and_sorted(self):
        a = contact("crm", tags=("vip", "customers"))
        b = contact("helpdesk", tags=("priority", "vip"))
        self.assertEqual(merge(a, b).tags, ("customers", "priority", "vip"))

    def test_refuses_to_merge_different_people(self):
        with self.assertRaises(ValueError):
            merge(contact("crm", contact_id="ct_a"), contact("crm", contact_id="ct_b"))

    def test_merge_all_collapses_by_id_and_preserves_order(self):
        crm_ada = CrmConnector.to_contact(canned_crm_pages()[0]["contacts"][0])
        hd_ada = HelpdeskConnector.to_contact(canned_helpdesk_pages()[0]["results"][0])
        grace = BillingWebhookConnector.to_contact(canned_billing_event())
        merged = merge_all([crm_ada, grace, hd_ada])
        self.assertEqual(len(merged), 2)
        self.assertEqual([c.email for c in merged], ["ada.lovelace@example.com", "grace@example.com"])
        ada = merged[0]
        self.assertEqual(ada.company, "Analytical Engines Pty Ltd", "CRM wins company")
        self.assertEqual(ada.phone, "+61290001111", "CRM wins phone it has")
        self.assertIn("priority", ada.tags, "helpdesk tag survived the merge")


if __name__ == "__main__":
    unittest.main()
