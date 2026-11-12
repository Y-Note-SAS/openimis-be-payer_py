"""
Security parity of payers and fundings: rows and writes stay within the user's regions /
districts. Payer.row_scope = LocationScope("location", loc_types R, D), a payer without
location (national) visible to all; Funding.row_scope = ParentScope("payer").

Fixture: core.test_helpers.create_security_tree -> R1>D1>W1>V1 / R2>D2>W2>V2; a payer in
D1, one in D2, one in R2 and a national one, a funding for the D1 and the D2 payers;
`user_a` holds the payer rights globally and is assigned district D1 only.
"""
import json

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import TestCase, override_settings

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import create_security_tree, create_right_only_user, get_test_token, perm_ids
from payer.apps import PayerConfig, DEFAULT_CFG
from payer.models import Payer, Funding
from payer.test_helpers import create_test_payer


def _u(*lists):
    return list(dict.fromkeys(r for lst in lists for r in lst))


PAYER_RIGHTS = _u(perm_ids(PayerConfig.gql_query_payers_perms),
                  perm_ids(PayerConfig.gql_mutation_payer_add_perms),
                  perm_ids(PayerConfig.gql_mutation_payer_update_perms),
                  perm_ids(PayerConfig.gql_mutation_payer_delete_perms))


def _product(code):
    """product.test_helpers imports program.test_helpers, absent on Cameroon's program."""
    from program.models import Program
    from product.models import Product
    program = Program.objects.create(code=code[:10], nameProgram=f"Program {code}")
    return Product.objects.create(
        code=code, name=f"Test product {code}", lump_sum=100, max_members=5,
        grace_period_enrolment=1, insurance_period=12, date_from="2019-01-01", date_to="2049-01-01",
        validity_from="2019-01-01", audit_user_id=-1, program=program)


def _build(cls, prefix):
    cls.t = create_security_tree(prefix)
    cls.product = _product(f"{prefix}PR")
    cls.payer_a = create_test_payer(custom_props={"name": f"{prefix}-a", "location": cls.t.d1})
    cls.payer_b = create_test_payer(custom_props={"name": f"{prefix}-b", "location": cls.t.d2})
    cls.payer_r2 = create_test_payer(custom_props={"name": f"{prefix}-r2", "location": cls.t.r2})
    cls.payer_nat = create_test_payer(custom_props={"name": f"{prefix}-nat"})
    cls.user_a = create_right_only_user(f"{prefix.lower()}user", PAYER_RIGHTS, [cls.t.d1.id])
    for key in ("a", "b"):
        funding = Funding(payer=getattr(cls, f"payer_{key}"), product=cls.product, amount=10,
                          pay_date="2024-01-01", receipt=f"{prefix}-f{key}")
        funding.save(user=cls.user_a)
        setattr(cls, f"funding_{key}", funding)


def _messages(result):
    return json.dumps(result or [], default=str).lower()


@override_settings(ROW_SECURITY=True)
class PayerReadScopeTest(openIMISGraphQLTestCase):

    @classmethod
    def setUpTestData(cls):
        _build(cls, "PYR")
        cls.token = get_test_token(cls.user_a)
        cls.no_right = create_right_only_user("pyrnoright", [], [cls.t.d1.id])
        cls.no_right_token = get_test_token(cls.no_right)

    def setUp(self):
        cache.clear()

    def _q(self, query, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return json.loads(self.query(query, headers=headers).content)

    def test_model_scope(self):
        names = set(Payer.get_queryset(Payer.objects.all(), self.user_a).values_list("name", flat=True))
        self.assertTrue({"PYR-a", "PYR-nat"} <= names, names)
        self.assertFalse({"PYR-b", "PYR-r2"} & names, names)

    def test_queryset_none_and_anonymous(self):
        """premium_action calls Payer.get_queryset(None, user); an anonymous user crashed (user._u)"""
        self.assertFalse(Payer.get_queryset(None, self.user_a).filter(uuid=self.payer_b.uuid).exists())
        self.assertTrue(Payer.get_queryset(None, self.user_a).filter(uuid=self.payer_a.uuid).exists())
        self.assertFalse(Payer.get_queryset(None, AnonymousUser()).exists())
        self.assertFalse(Funding.get_queryset(None, AnonymousUser()).exists())

    def test_queryset_keeps_the_incoming_filter(self):
        """the incoming queryset was discarded (queryset=Payer.objects)"""
        rows = Payer.get_queryset(Payer.objects.filter(uuid=self.payer_nat.uuid), self.user_a)
        self.assertEqual(["PYR-nat"], list(rows.values_list("name", flat=True)))

    def test_payers_listed_within_the_districts(self):
        content = self._q('{ payers { edges { node { name } } } }', self.token)
        self.assertNotIn("errors", content, content.get("errors"))
        names = {e["node"]["name"] for e in content["data"]["payers"]["edges"]}
        self.assertTrue({"PYR-a", "PYR-nat"} <= names, names)
        self.assertFalse({"PYR-b", "PYR-r2"} & names, names)

    def test_payer_by_uuid_out_of_scope_not_found(self):
        content = self._q(f'{{ payer(uuid: "{self.payer_b.uuid}") {{ name }} }}', self.token)
        self.assertIsNone((content.get("data") or {}).get("payer"), content)
        content = self._q(f'{{ payer(uuid: "{self.payer_a.uuid}") {{ name }} }}', self.token)
        self.assertEqual("PYR-a", content["data"]["payer"]["name"], content)

    def test_fundings_within_the_payers_in_scope(self):
        """fundings had no resolver: the whole Funding table"""
        content = self._q('{ fundings { edges { node { receipt } } } }', self.token)
        self.assertNotIn("errors", content, content.get("errors"))
        receipts = {e["node"]["receipt"] for e in content["data"]["fundings"]["edges"]}
        self.assertIn("PYR-fa", receipts)
        self.assertNotIn("PYR-fb", receipts)

    def test_fundings_need_the_right(self):
        content = self._q('{ fundings { edges { node { receipt } } } }', self.no_right_token)
        self.assertTrue(content.get("errors"), content)

    def test_funding_by_uuid(self):
        """funding(uuid) had no resolver"""
        content = self._q(f'{{ funding(uuid: "{self.funding_a.id}") {{ receipt }} }}', self.token)
        self.assertEqual("PYR-fa", ((content.get("data") or {}).get("funding") or {}).get("receipt"), content)
        content = self._q(f'{{ funding(uuid: "{self.funding_b.id}") {{ receipt }} }}', self.token)
        self.assertIsNone((content.get("data") or {}).get("funding"), content)


@override_settings(ROW_SECURITY=True)
class PayerWriteScopeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        _build(cls, "PYW")

    def setUp(self):
        cache.clear()

    def _input(self, location, **extra):
        return {"name": "NEW", "type": Payer.PAYER_TYPE_OTHER, "location_uuid": location.uuid, **extra}

    def test_update_right_is_in_the_payer_series(self):
        """122103 is the medical items update right"""
        self.assertEqual(["121803"], DEFAULT_CFG["gql_mutation_payer_update_perms"])

    def test_create_in_scope_allowed(self):
        from payer.gql_mutations import CreatePayerMutation
        self.assertIsNone(CreatePayerMutation.async_mutate(self.user_a, **self._input(self.t.d1, name="NEW-d1")))
        self.assertEqual(self.t.d1.id, Payer.objects.get(name="NEW-d1").location_id)

    def test_create_out_of_scope_refused(self):
        from payer.gql_mutations import CreatePayerMutation
        self.assertTrue(CreatePayerMutation.async_mutate(self.user_a, **self._input(self.t.d2, name="NEW-d2")))
        self.assertFalse(Payer.objects.filter(name="NEW-d2").exists())

    def test_update_out_of_scope_refused(self):
        from payer.gql_mutations import UpdatePayerMutation
        result = UpdatePayerMutation.async_mutate(
            self.user_a, **self._input(self.t.d1, uuid=self.payer_b.uuid, name="MOVED"))
        self.assertTrue(result)
        self.assertEqual(("PYW-b", self.t.d2.id), tuple(
            Payer.objects.filter(id=self.payer_b.id).values_list("name", "location_id").get()))

    def test_update_moving_out_of_scope_refused(self):
        from payer.gql_mutations import UpdatePayerMutation
        result = UpdatePayerMutation.async_mutate(
            self.user_a, **self._input(self.t.d2, uuid=self.payer_a.uuid, name="PYW-a"))
        self.assertTrue(result)
        self.assertEqual(self.t.d1.id, Payer.objects.get(id=self.payer_a.id).location_id)

    def test_update_in_scope_allowed(self):
        from payer.gql_mutations import UpdatePayerMutation
        self.assertIsNone(UpdatePayerMutation.async_mutate(
            self.user_a, **self._input(self.t.d1, uuid=self.payer_a.uuid, name="RENAMED")))
        self.assertEqual("RENAMED", Payer.objects.get(id=self.payer_a.id).name)

    def test_delete_out_of_scope_does_not_exist(self):
        from payer.gql_mutations import DeletePayerMutation
        result = DeletePayerMutation.async_mutate(self.user_a, uuids=[str(self.payer_b.uuid)])
        self.assertTrue(result)
        self.assertIsNone(Payer.objects.get(id=self.payer_b.id).validity_to)

    def test_delete_unknown_uuids_give_their_own_error(self):
        """`% {"id", uuid}` was a set: a TypeError instead of the error"""
        from payer.gql_mutations import DeletePayerMutation
        unknown = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
        result = DeletePayerMutation.async_mutate(self.user_a, uuids=unknown)
        self.assertEqual(unknown, [error.get("title") for error in result])

    def test_delete_in_scope_allowed(self):
        from payer.gql_mutations import DeletePayerMutation
        self.assertEqual([], DeletePayerMutation.async_mutate(self.user_a, uuids=[str(self.payer_a.uuid)]))
        self.assertIsNotNone(Payer.objects.get(id=self.payer_a.id).validity_to)

    def _add_funding(self, payer, receipt):
        from payer.gql_mutations import AddFundingMutation
        return AddFundingMutation.async_mutate(self.user_a, **{
            "payer_id": payer.id, "product_id": self.product.id, "pay_date": "2024-02-01",
            "amount": 5, "receipt": receipt})

    def test_add_funding_to_a_payer_out_of_scope_refused(self):
        self.assertTrue(self._add_funding(self.payer_b, "ADD-b"))
        self.assertFalse(Funding.objects.filter(receipt="ADD-b").exists())

    def test_add_funding_in_scope_allowed(self):
        self.assertIsNone(self._add_funding(self.payer_a, "ADD-a"))
        self.assertTrue(Funding.objects.filter(receipt="ADD-a", payer=self.payer_a).exists())
