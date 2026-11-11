from django.apps import AppConfig

MODULE_NAME = "payer"

DEFAULT_CFG = {
    "gql_query_payers_perms": ["121801"],
    "gql_mutation_payer_add_perms": ["121802"],
    "gql_mutation_payer_update_perms": ["121803"],
    "gql_mutation_payer_delete_perms": ["121804"],
}


class PayerConfig(AppConfig):
    name = MODULE_NAME

    gql_query_payers_perms = []
    gql_mutation_payer_add_perms = []
    gql_mutation_payer_update_perms = []
    gql_mutation_payer_delete_perms = []

    def __load_config(self, cfg):
        for field in cfg:
            if hasattr(PayerConfig, field):
                setattr(PayerConfig, field, cfg[field])

    def ready(self):
        from core.models import ModuleConfiguration

        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self.__load_config(cfg)
