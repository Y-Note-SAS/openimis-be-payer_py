import graphene
from django.db.models import Q
from django.core.exceptions import PermissionDenied
import graphene_django_optimizer as gql_optimizer
from core.gql import legacy_row_filters, row_filter_arguments
from core.models import Audits, Validity
from django.utils.translation import gettext as _, gettext_lazy
from payer.apps import PayerConfig
from core.schema import OrderedDjangoFilterConnectionField
from .models import Payer, Funding
from product.schema import ProductGQLType

from .gql_queries import PayerGQLType, FundingGQLType

from .gql_mutations import (
    CreatePayerMutation,
    UpdatePayerMutation,
    DeletePayerMutation,
    AddFundingMutation,
)


class Query(graphene.ObjectType):
    fundings = OrderedDjangoFilterConnectionField(
        FundingGQLType,
        payer_id=graphene.ID(),
        client_mutation_id=graphene.String(),
        **row_filter_arguments(),
        parent_location_level=graphene.Int(),
        orderBy=graphene.List(of_type=graphene.String),
    )
    
    funding = graphene.Field(FundingGQLType, uuid=graphene.UUID())

        
    payers = OrderedDjangoFilterConnectionField(
        PayerGQLType,
        **row_filter_arguments(),
        search=graphene.String(
            description=gettext_lazy("Search in `name`, `phone` & `email`")
        ),
        orderBy=graphene.List(of_type=graphene.String),
    )
    payer = graphene.Field(PayerGQLType, uuid=graphene.UUID())

    def resolve_fundings(self, info, **kwargs):
        # it had no resolver: the whole Funding table, without any right
        if not info.context.user.has_perms(PayerConfig.gql_query_payers_perms):
            raise PermissionDenied(_("unauthorized"))
        # narrowed to the fundings of the payers the user may see by Funding.get_queryset
        # (FundingGQLType); the ones not deleted by default, showHistory adds the deleted
        legacy_row_filters(
            info, kwargs, validity=Validity.ALL if kwargs.get("show_history") else Validity.NOW)
        return Funding.objects.all()

    def resolve_funding(self, info, uuid=None, **kwargs):
        # it had no resolver (always null); within the user's scope
        if not info.context.user.has_perms(PayerConfig.gql_query_payers_perms):
            raise PermissionDenied(_("unauthorized"))
        if uuid is None:
            return None
        return Funding.get_queryset(
            Funding.objects.filter(id=uuid), info.context.user, validity=Validity.ALL).first()

    def resolve_payer(self, info, uuid, **kwargs):
        if not info.context.user.has_perms(PayerConfig.gql_query_payers_perms):
            raise PermissionDenied(_("unauthorized"))
        # a lookup by uuid: whatever its validity (as before), within the user's scope
        return Payer.get_queryset(
            Payer.objects.filter(uuid=uuid), info.context.user, validity=Validity.ALL).first()

    def resolve_payers(self, info, **kwargs):
        if not info.context.user.has_perms(PayerConfig.gql_query_payers_perms):
            raise PermissionDenied(_("unauthorized"))

        filters = Payer.objects.all()
        # validity / audits and the row scope are applied by Payer.get_queryset
        # (PayerGQLType, OP-2029); showHistory keeps its legacy meaning: no validity filter
        show_history = kwargs.get("show_history", False)
        legacy_row_filters(
            info, kwargs,
            validity=Validity.ALL if show_history else Validity.NOW,
            audits=Audits.ALL if show_history else None)

        search = kwargs.get("search", None)
        if search is not None:
            filters = filters.filter(
                Q(name__icontains=search)
                | Q(phone__icontains=search)
                | Q(email__icontains=search)
            )

        return gql_optimizer.query(filters, info)


class Mutation(graphene.ObjectType):
    create_payer = CreatePayerMutation.Field()
    update_payer = UpdatePayerMutation.Field()
    delete_payer = DeletePayerMutation.Field()
    add_funding = AddFundingMutation.Field()
