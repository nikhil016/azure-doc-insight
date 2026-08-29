import os

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.servicebus import ServiceBusClient
from azure.storage.blob import BlobServiceClient

COSMOS_DATABASE = "ai200-doc-insight"
COSMOS_DOCUMENTS_CONTAINER = "documents"

_credential = None
_secret_client = None
_secret_cache = {}


def get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def get_secret(name: str) -> str:
    global _secret_client
    if name in _secret_cache:
        return _secret_cache[name]
    if _secret_client is None:
        _secret_client = SecretClient(vault_url=os.environ["KEY_VAULT_URL"], credential=get_credential())
    value = _secret_client.get_secret(name).value
    _secret_cache[name] = value
    return value


def get_documents_container():
    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], get_secret("cosmos-primary-key"))
    return client.get_database_client(COSMOS_DATABASE).get_container_client(COSMOS_DOCUMENTS_CONTAINER)


def get_blob_service_client():
    account_url = f"https://{os.environ['STORAGE_ACCOUNT_NAME']}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=get_credential())


def get_service_bus_client():
    fqdn = os.environ["SERVICE_BUS_NAMESPACE_FQDN"]
    return ServiceBusClient(fully_qualified_namespace=fqdn, credential=get_credential())
