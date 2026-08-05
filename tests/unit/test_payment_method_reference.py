"""Which payment methods need a reference before they can be recorded (#137).

The rule is mbe-api's because the SAT catalog is: a client keeping its own copy goes stale the
moment a code is added or reclassified, with nothing to signal that it has. These tests pin both
the classification and — more importantly — the direction it fails in for a code nobody has
classified yet.
"""

from decimal import Decimal

from app.enums import EntityStatus, PaymentMethod
from app.schemas.core import PaymentMethodOptionResponse


class TestRequiresReference:
    def test_bank_card_and_voucher_methods_require_one(self) -> None:
        assert PaymentMethod.requires_reference(PaymentMethod.CHECK)
        assert PaymentMethod.requires_reference(PaymentMethod.EFT)
        assert PaymentMethod.requires_reference(PaymentMethod.CREDIT_CARD)
        assert PaymentMethod.requires_reference(PaymentMethod.DEBIT_CARD)
        assert PaymentMethod.requires_reference(PaymentMethod.SERVICE_CARD)
        assert PaymentMethod.requires_reference(PaymentMethod.ELECTRONIC_PURSE)
        assert PaymentMethod.requires_reference(PaymentMethod.ELECTRONIC_MONEY)
        assert PaymentMethod.requires_reference(PaymentMethod.FOOD_VOUCHERS)

    def test_cash_and_in_kind_settlements_do_not(self) -> None:
        """Nothing external is issued, so there is no identifier to quote."""
        assert not PaymentMethod.requires_reference(PaymentMethod.CASH)
        assert not PaymentMethod.requires_reference(PaymentMethod.GIVING)
        assert not PaymentMethod.requires_reference(PaymentMethod.ADVANCE_PAYMENTS)
        assert not PaymentMethod.requires_reference(
            PaymentMethod.TO_THE_SATISFACTION_OF_THE_CREDITOR
        )
        assert not PaymentMethod.requires_reference(PaymentMethod.NA)

    def test_an_unclassified_sat_code_does_not_block_a_cashier(self) -> None:
        """15 (condonación) and 17 (compensación) are real SAT codes this enum does not name.

        The permissive default is the deliberate half of the rule: an unclassified code must not
        stop money being taken until someone gets round to placing it.
        """
        assert not PaymentMethod.requires_reference(15)
        assert not PaymentMethod.requires_reference(17)


def _option(payment_method: int) -> PaymentMethodOptionResponse:
    return PaymentMethodOptionResponse.model_validate(
        {
            'payment_method_option_id': 1,
            'facility': {
                'facility_id': 1,
                'code': 'MTY',
                'name': 'Monterrey',
                'type': 0,
                'location': 'Monterrey',
                'address': 1,
                'taxpayer': 'AAA010101AAA',
                'logo': None,
                'receipt_message': None,
                'default_batch': None,
                'status': EntityStatus.ACTIVE,
            },
            'warehouse': None,
            'name': 'Tarjeta',
            'number_of_payments': 1,
            'display_on_ticket': True,
            'payment_method': payment_method,
            'commission': Decimal('0'),
            'status': EntityStatus.ACTIVE,
        }
    )


class TestOptionResponseExposesTheRule:
    def test_a_card_option_reports_that_it_needs_a_reference(self) -> None:
        assert _option(PaymentMethod.CREDIT_CARD).requires_reference is True

    def test_a_cash_option_reports_that_it_does_not(self) -> None:
        assert _option(PaymentMethod.CASH).requires_reference is False

    def test_a_legacy_code_outside_the_enum_serialises_rather_than_raising(self) -> None:
        """`payment_method` is a plain int column — an unnamed code must not 500 the listing."""
        assert _option(15).model_dump()['requires_reference'] is False
