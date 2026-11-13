from gettext import gettext as _
import logging
import graphene
from graphene.relay import Node
from core.schema import OpenIMISMutation
from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from .apps import PayerConfig
from location.models import Location
from .models import Payer, PayerMutation, Funding


logger = logging.getLogger(__name__)


def scoped_payer(user, **lookup):
    """The current payer matching `lookup` if `user` may see it (Payer.get_queryset)."""
    return Payer.get_queryset(Payer.objects.filter(**lookup), user).first()


def scoped_location(user, location_uuid):
    """
    The location `location_uuid` if a payer placed there would be visible to `user`
    (same rule as Payer.row_scope: the user's regions / districts), else None.
    """
    location = Location.objects.filter(uuid=location_uuid, validity_to__isnull=True).first()
    if location is None or not settings.ROW_SECURITY:
        return location
    if user is None or getattr(user, "is_anonymous", True):
        return None
    in_scope = Location.objects.filter(id=location.id).filter(
        *Payer.filter_location(user, paths=("id",)))
    return location if in_scope.exists() else None


def create_or_update_payer(user, data, update=None):
    """
    Create (`update` False) or update (`update` True) a payer; `update` None decides on
    the presence of a uuid. The payer updated is looked up within the user's scope, and
    the location set must be in it too (a payer the user could not see afterwards).
    """
    client_mutation_id = data.pop("client_mutation_id", None)
    data.pop("client_mutation_label", None)
    # the row is designated by its uuid only: an id from the client is ignored
    data.pop("id", None)
    payer_uuid = data.pop("uuid", None)
    location_uuid = data.pop("location_uuid", None)
    if update is None:
        update = bool(payer_uuid)

    location = None
    if location_uuid is not None:
        location = scoped_location(user, location_uuid)
        if location is None:
            raise PermissionDenied(_("unauthorized"))

    if update:
        payer = scoped_payer(user, uuid=payer_uuid) if payer_uuid else None
        if payer is None:
            raise ValidationError(_("payer.validation.id_does_not_exist"))
        if payer.validity_to:
            raise ValidationError(_('Cannot update historical values'))
        payer.save_history()
        for (key, value) in data.items():
            setattr(payer, key, value)
    else:
        payer = Payer(**data)

    if location is not None:
        payer.location = location

    payer.save()

    if client_mutation_id:
        PayerMutation.object_mutated(
            user, client_mutation_id=client_mutation_id, payer=payer
        )


class CreateOrUpdatePayerMutation(OpenIMISMutation):
    @classmethod
    def do_mutate(cls, perms, user, update=None, **data):
        if type(user) is AnonymousUser or not user.id:
            raise ValidationError(_("mutation.authentication_required"))
        if not user.has_perms(perms):
            raise PermissionDenied(_("unauthorized"))

        data["audit_user_id"] = user.id_for_audit

        return create_or_update_payer(user, data, update=update)


class PayerInputType(OpenIMISMutation.Input):
    name = graphene.String(required=True)
    email = graphene.String()
    phone = graphene.String()
    fax = graphene.String()
    address = graphene.String()
    location_uuid = graphene.UUID(required=True)
    type = graphene.String(required=True)


class CreatePayerMutation(CreateOrUpdatePayerMutation):
    _mutation_module = "payer"
    _mutation_class = "CreatePayerMutation"

    class Input(PayerInputType):
        pass

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            cls.do_mutate(
                PayerConfig.gql_mutation_payer_add_perms,
                user,
                update=False,
                **data,
            )
        except Exception as exc:
            logger.exception(exc)
            return [
                {
                    "message": _("payer.mutation.failed_to_create_payer"),
                    "detail": str(exc),
                }
            ]


class UpdatePayerMutation(CreateOrUpdatePayerMutation):
    _mutation_module = "payer"
    _mutation_class = "UpdatePayerMutation"

    class Input(PayerInputType):
        uuid = graphene.UUID(required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            cls.do_mutate(
                PayerConfig.gql_mutation_payer_update_perms,
                user,
                update=True,
                **data,
            )
        except Exception as exc:
            logger.exception(exc)
            return [
                {
                    "message": _("payer.mutation.failed_to_update_payer")
                    % {"uuid": data.get("uuid")},
                    "detail": str(exc),
                }
            ]


class DeletePayerMutation(OpenIMISMutation):
    _mutation_module = "payer"
    _mutation_class = "DeletePayerMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(PayerConfig.gql_mutation_payer_delete_perms):
            raise PermissionDenied(_("unauthorized"))
        errors = []

        for uuid in data["uuids"]:
            # a payer out of the user's scope (or already deleted) does not exist for them
            obj = scoped_payer(user, uuid=uuid)
            if obj is None:
                errors.append(
                    {
                        "title": uuid,
                        "list": [
                            {
                                "message": _("payer.validation.id_does_not_exist")
                                % {"id": uuid}
                            }
                        ],
                    }
                )
                continue
            try:
                obj.delete_history()
            except Exception as exc:
                logger.exception(exc)
                errors.append(
                    {
                        "title": uuid,
                        "list": [
                            {
                                "message": _("payer.mutation.failed_to_delete_payer")
                                % {"uuid": obj.uuid},
                                "detail": str(exc),
                            }
                        ],
                    }
                )

        if len(errors) == 1:
            errors = errors[0]["list"]
        return errors


class AddFundingMutation(OpenIMISMutation):
    _mutation_module = "payer"
    _mutation_class = "AddFundingMutation"

    class Input(OpenIMISMutation.Input):
        payer_id = graphene.ID(required=True)
        product_id = graphene.ID(required=True)
        pay_date = graphene.Date(required=True)
        amount = graphene.Decimal(required=True)
        receipt = graphene.String(required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        client_mutation_id = data.get("client_mutation_id", None)
        if not user.has_perms(PayerConfig.gql_mutation_payer_update_perms):
            raise PermissionDenied(_("unauthorized"))

        from product.models import Product

        try:
            # the payer and the product: within the user's scope
            payer = scoped_payer(user, id=data.get("payer_id"))
            products = Product.objects.filter(validity_to__isnull=True, id=data.get("product_id"))
            if settings.ROW_SECURITY:
                products = Product.get_queryset(products, user)
            product = products.first()
            if payer is None or product is None:
                raise PermissionDenied(_("unauthorized"))
        except Exception as exc:
            return [
                {
                    "message": _("payer.mutation.failed_to_add_funding"),
                    "detail": str(exc),
                }
            ]

        try:
            funding = Funding(**{
                'payer':payer,
                'product': product,
                'pay_date':data.get("pay_date"),
                'amount':data.get("amount"),
                'receipt':data.get("receipt"),
            }
            )
            funding.save(username = user.username)

        except Exception as exc:
            logger.exception(exc)
            return [
                {
                    "message": _("payer.mutation.failed_to_add_funding"),
                    "detail": str(exc),
                }
            ]
