try:
    from services.auth_api.schemas.api_keys import APIKeyCreateInput, APIKeyVerifyInput
    from services.auth_api.schemas.compliance import ComplianceSettingsInput, PiiScanInput
    from services.auth_api.schemas.ip_allowlists import IPAllowlistCreateInput
    from services.auth_api.schemas.secrets import SecretCreateInput, SignPayload, VerifyPayload
except ModuleNotFoundError:
    from auth_api.schemas.api_keys import APIKeyCreateInput, APIKeyVerifyInput
    from auth_api.schemas.compliance import ComplianceSettingsInput, PiiScanInput
    from auth_api.schemas.ip_allowlists import IPAllowlistCreateInput
    from auth_api.schemas.secrets import SecretCreateInput, SignPayload, VerifyPayload

APIKeyIn = APIKeyCreateInput
ComplianceSettingsIn = ComplianceSettingsInput
IPRuleIn = IPAllowlistCreateInput
PiiScanIn = PiiScanInput
SecretIn = SecretCreateInput
SignIn = SignPayload
VerifyIn = VerifyPayload

__all__ = [
    "APIKeyCreateInput",
    "APIKeyIn",
    "APIKeyVerifyInput",
    "ComplianceSettingsInput",
    "ComplianceSettingsIn",
    "IPAllowlistCreateInput",
    "IPRuleIn",
    "PiiScanInput",
    "PiiScanIn",
    "SecretCreateInput",
    "SecretIn",
    "SignPayload",
    "SignIn",
    "VerifyPayload",
    "VerifyIn",
]
