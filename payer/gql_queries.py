import graphene
from graphene_django import DjangoObjectType
from django.db.models import Q
from django import forms
from django.utils.translation import gettext_lazy

from invoice.apps import InvoiceConfig
from .apps import PayerConfig
from .models import Payer, Funding
from core import ExtendedConnection
from core.gql.scoped_object_type import ScopedQuerysetMixin
from core.models import filter_validity

from product.schema import ProductGQLType
import django_filters
from django.utils.translation import gettext as _
from django.core.exceptions import PermissionDenied


class IntegerFilter(django_filters.NumberFilter):
    field_class = forms.IntegerField
class CharFilter(django_filters.CharFilter):
    field_class = forms.CharField


class UUIDFilter(django_filters.UUIDFilter):
    field_class = forms.UUIDField
    
class FundingFilter(django_filters.FilterSet):
    uuid = UUIDFilter(
        lookup_expr=["exact"],
        method="filter_uuid",
        label=gettext_lazy("Filter funding by UUID"),
    )
    
    def filter_uuid(self, queryset, name, value):
        return queryset.filter(id = value)
    
    class Meta:
        model = Funding
        fields = {
            "id": ["exact"],
            "product": ["exact"],
            "pay_date": ["exact", "lt", "lte", "gt", "gte"],
            "receipt": ["exact", "icontains"],
            "amount": ["exact", "lt", "lte", "gt", "gte"],
            "status": ["exact"],

        }
class PayerFilter(django_filters.FilterSet):
    location = IntegerFilter(
        lookup_expr=["exact"],
        method="filter_location",
        label=gettext_lazy("Filter payers with or below a given location ID"),
    )
    type = CharFilter(
        lookup_expr=["exact"],
        method="filter_type",
        label=gettext_lazy("Filter payers with or below a given payer type code"),
    )

    def filter_location(self, queryset, name, value):
        return queryset.filter(Q(location__id=value) | Q(location__parent__id=value))
    def filter_type(self, queryset, name, value):
            return queryset.filter(type=value)
    class Meta:
        model = Payer
        fields = {
            "id": ["exact"],
            "uuid": ["exact"],
            "name": ["exact", "icontains"],
            "email": ["exact", "icontains"],
            "phone": ["exact", "icontains"],
        }


class FundingGQLType(ScopedQuerysetMixin, DjangoObjectType):
    uuid = graphene.UUID()
    
    def resolve_uuid(self, info, **kwargs):
        return self.id
    class Meta:
        model = Funding
        interfaces = (graphene.relay.Node,)
        filterset_class = FundingFilter
        connection_class = ExtendedConnection

class FundingConnection(graphene.Connection):
    class Meta:
        node = FundingGQLType

    total_count = graphene.Int()

    def resolve_total_count(self, info, **kwargs):
        if not info.context.user.has_perms(PayerConfig.gql_query_payers_perms):
            raise PermissionDenied(_("unauthorized"))
        return len(self.iterable)


class PayerGQLType(ScopedQuerysetMixin, DjangoObjectType):

    #premiums = graphene.relay.ConnectionField(FundingConnection)
    fundings = graphene.relay.ConnectionField(FundingConnection)

    def resolve_fundings(self, info, **kwargs):
        if not info.context.user.has_perms(PayerConfig.gql_query_payers_perms):
            raise PermissionDenied(_("unauthorized"))
        return (
            self.fundings.order_by("-pay_date").all()
        )

    def resolve_premiums(self, info, **kwargs):
        if not info.context.user.has_perms(PayerConfig.gql_query_payers_perms):
            raise PermissionDenied(_("unauthorized"))
        return (
            self.premuims.fitler(*filter_validity()).order_by("-pay_date").all()
        )

    class Meta:
        model = Payer
        interfaces = (graphene.relay.Node,)
        filterset_class = PayerFilter
        connection_class = ExtendedConnection

        exclude_fields = ("premiums",)
